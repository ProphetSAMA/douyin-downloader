App({
  onLaunch() {
    wx.cloud.init({
      env: "prod-d1g622olj4f1fa950",
      traceUser: true
    });
  },
  globalData: {}
});
