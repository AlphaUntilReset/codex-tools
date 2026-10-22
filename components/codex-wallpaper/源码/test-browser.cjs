const { chromium } = require('playwright');
module.exports.chromium = {
  launch(options = {}) {
    const channel = process.env.CODEX_THEME_BROWSER_CHANNEL;
    return chromium.launch({ ...options, ...(channel ? { channel } : {}) });
  },
  connectOverCDP: (...args) => chromium.connectOverCDP(...args),
};
