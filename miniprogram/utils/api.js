const config = require("../config");

function getBaseUrl() {
  return String(config.apiBaseUrl || "").replace(/\/+$/, "");
}

function joinUrl(path) {
  const cleanPath = String(path || "").replace(/^\/+/, "");
  return `${getBaseUrl()}/${cleanPath}`;
}

function absoluteUrl(value) {
  const url = String(value || "");
  if (!url) {
    return "";
  }
  if (/^https?:\/\//i.test(url)) {
    return url;
  }
  return joinUrl(url);
}

function request(options) {
  return new Promise((resolve, reject) => {
    wx.request({
      url: joinUrl(options.path),
      method: options.method || "GET",
      data: options.data || {},
      header: {
        "content-type": "application/json"
      },
      success(res) {
        if (res.statusCode >= 200 && res.statusCode < 300) {
          resolve(res.data);
          return;
        }
        const detail = res.data && res.data.detail ? res.data.detail : `HTTP ${res.statusCode}`;
        reject(new Error(detail));
      },
      fail(error) {
        reject(new Error(error.errMsg || "request failed"));
      }
    });
  });
}

function getHealth() {
  return request({ path: "/api/v1/health" });
}

function parseVideo(url) {
  return request({
    path: "/api/v1/parse",
    method: "POST",
    data: { url }
  });
}

function createDownload(url) {
  return request({
    path: "/api/v1/download",
    method: "POST",
    data: { url }
  });
}

function listJobs() {
  return request({ path: "/api/v1/jobs" });
}

function getJob(jobId) {
  return request({ path: `/api/v1/jobs/${jobId}` });
}

module.exports = {
  absoluteUrl,
  getBaseUrl,
  getHealth,
  parseVideo,
  createDownload,
  listJobs,
  getJob
};
