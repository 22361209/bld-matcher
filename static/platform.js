const platform = navigator.userAgentData?.platform || navigator.platform || navigator.userAgent;

if (/win/i.test(platform) || /Windows NT/i.test(navigator.userAgent)) {
  document.documentElement.dataset.platform = "windows";
}
