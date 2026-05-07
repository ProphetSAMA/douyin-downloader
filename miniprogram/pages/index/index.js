const api = require("../../utils/api");

const PARSE_DEBOUNCE_MS = 700;

function normalizeParsedVideo(data) {
  const video = data.video || {};
  const author = data.author || {};
  return {
    awemeId: data.aweme_id || "",
    title: data.title || "未命名视频",
    authorName: author.nickname || "未知作者",
    coverUrl: api.absoluteUrl(data.cover_url),
    previewUrl: api.absoluteUrl(video.preview_url),
    downloadUrl: api.absoluteUrl(video.download_url),
    filename: video.filename || "douyin_video.mp4",
    resolvedUrl: data.resolved_url || "",
    duration: Number(data.duration || 0)
  };
}

function hasLink(text) {
  return /(https?:\/\/|douyin\.com\/|v\.douyin\.com\/|v\.iesdouyin\.com\/|iesdouyin\.com\/)/i.test(
    String(text || "")
  );
}

Page({
  data: {
    inputText: "",
    video: null,
    error: "",
    healthText: "检查中",
    healthClass: "checking",
    parsing: false,
    saving: false
  },

  onLoad() {
    this.refreshHealth();
  },

  onUnload() {
    if (this.parseTimer) {
      clearTimeout(this.parseTimer);
    }
  },

  onPullDownRefresh() {
    this.refreshHealth().then(() => wx.stopPullDownRefresh());
  },

  refreshHealth() {
    return api
      .getHealth()
      .then(() => {
        this.setData({
          healthText: "服务正常",
          healthClass: "ok"
        });
      })
      .catch(() => {
        this.setData({
          healthText: "服务异常",
          healthClass: "down"
        });
      });
  },

  onInput(event) {
    const inputText = event.detail.value;
    this.setData({
      inputText,
      error: "",
      video: hasLink(inputText) ? this.data.video : null
    });
    this.scheduleParse();
  },

  pasteFromClipboard() {
    wx.getClipboardData({
      success: (res) => {
        const inputText = String(res.data || "").trim();
        this.setData({
          inputText,
          error: "",
          video: null
        });
        if (inputText) {
          this.parseCurrent();
        }
      },
      fail: () => {
        wx.showToast({ title: "读取剪贴板失败", icon: "none" });
      }
    });
  },

  clearInput() {
    if (this.parseTimer) {
      clearTimeout(this.parseTimer);
    }
    this.setData({
      inputText: "",
      video: null,
      error: "",
      parsing: false
    });
  },

  scheduleParse() {
    if (this.parseTimer) {
      clearTimeout(this.parseTimer);
    }
    if (!hasLink(this.data.inputText)) {
      return;
    }
    this.parseTimer = setTimeout(() => {
      this.parseCurrent();
    }, PARSE_DEBOUNCE_MS);
  },

  parseCurrent() {
    const inputText = String(this.data.inputText || "").trim();
    if (!inputText) {
      wx.showToast({ title: "请先粘贴链接", icon: "none" });
      return;
    }
    if (this.data.parsing) {
      return;
    }

    this.setData({
      parsing: true,
      error: ""
    });
    api
      .parseVideo(inputText)
      .then((data) => {
        this.setData({
          video: normalizeParsedVideo(data),
          error: ""
        });
      })
      .catch((error) => {
        this.setData({
          video: null,
          error: error.message || "解析失败"
        });
      })
      .then(() => {
        this.setData({ parsing: false });
      });
  },

  saveVideo() {
    const video = this.data.video;
    if (!video || !video.downloadUrl) {
      wx.showToast({ title: "请先解析视频", icon: "none" });
      return;
    }
    if (this.data.saving) {
      return;
    }

    this.setData({ saving: true });
    wx.showLoading({ title: "下载中" });
    wx.downloadFile({
      url: video.downloadUrl,
      success: (downloadRes) => {
        if (downloadRes.statusCode < 200 || downloadRes.statusCode >= 300) {
          wx.hideLoading();
          this.setData({ saving: false });
          wx.showToast({ title: `下载失败：${downloadRes.statusCode}`, icon: "none" });
          return;
        }
        wx.saveVideoToPhotosAlbum({
          filePath: downloadRes.tempFilePath,
          success: () => {
            wx.hideLoading();
            this.setData({ saving: false });
            wx.showToast({ title: "已保存到相册", icon: "success" });
          },
          fail: (error) => {
            wx.hideLoading();
            this.setData({ saving: false });
            if (String(error.errMsg || "").includes("auth deny")) {
              wx.showModal({
                title: "需要相册权限",
                content: "请在设置中允许保存到相册。",
                confirmText: "去设置",
                success: (modalRes) => {
                  if (modalRes.confirm) {
                    wx.openSetting();
                  }
                }
              });
              return;
            }
            wx.showToast({ title: "保存失败", icon: "none" });
          }
        });
      },
      fail: (error) => {
        wx.hideLoading();
        this.setData({ saving: false });
        wx.showToast({ title: error.errMsg || "下载失败", icon: "none" });
      }
    });
  },

  copyResolvedUrl() {
    const video = this.data.video;
    if (!video || !video.resolvedUrl) {
      return;
    }
    wx.setClipboardData({ data: video.resolvedUrl });
  }
});
