/* Theme definitions, live preview, and persistence for the guided screens.
   Runs before the stylesheet so the saved theme is applied with no flash. */
(function () {
  'use strict';
  var KEY = 'repotrace-theme';
  var THEMES = [
    {id: 'one-dark-pro', name: 'One Dark Pro', kind: 'dark', bg: '#282c34', surface: '#21252b', border: '#3e4452', text: '#abb2bf', muted: '#9aa3b2', accent: '#61afef', success: '#98c379', warning: '#e5c07b', error: '#ff7b86'},
    {id: 'dracula', name: 'Dracula', kind: 'dark', bg: '#282a36', surface: '#21222c', border: '#44475a', text: '#f8f8f2', muted: '#c3c7e0', accent: '#bd93f9', success: '#50fa7b', warning: '#f1fa8c', error: '#ff5555'},
    {id: 'tokyo-night', name: 'Tokyo Night', kind: 'dark', bg: '#1a1b26', surface: '#16161e', border: '#2f3549', text: '#c0caf5', muted: '#a9b1d6', accent: '#7aa2f7', success: '#9ece6a', warning: '#e0af68', error: '#f7768e'},
    {id: 'catppuccin-mocha', name: 'Catppuccin Mocha', kind: 'dark', bg: '#1e1e2e', surface: '#181825', border: '#313244', text: '#cdd6f4', muted: '#bac2de', accent: '#cba6f7', success: '#a6e3a1', warning: '#f9e2af', error: '#f38ba8'},
    {id: 'nord', name: 'Nord', kind: 'dark', bg: '#2e3440', surface: '#3b4252', border: '#4c566a', text: '#eceff4', muted: '#d8dee9', accent: '#88c0d0', success: '#a3be8c', warning: '#ebcb8b', error: '#ff9aa3'},
    {id: 'gruvbox-dark', name: 'Gruvbox Dark', kind: 'dark', bg: '#282828', surface: '#1d2021', border: '#504945', text: '#ebdbb2', muted: '#d5c4a1', accent: '#83a598', success: '#b8bb26', warning: '#fabd2f', error: '#ff7a66'},
    {id: 'solarized-dark', name: 'Solarized Dark', kind: 'dark', bg: '#002b36', surface: '#073642', border: '#586e75', text: '#fdf6e3', muted: '#c0c8c4', accent: '#4cb3f5', success: '#859900', warning: '#b58900', error: '#ff7a72'},
    {id: 'monokai', name: 'Monokai', kind: 'dark', bg: '#272822', surface: '#1e1f1c', border: '#49483e', text: '#f8f8f2', muted: '#cfcfc2', accent: '#66d9ef', success: '#a6e22e', warning: '#e6db74', error: '#ff6b9d'},
    {id: 'github-dark', name: 'GitHub Dark', kind: 'dark', bg: '#0d1117', surface: '#161b22', border: '#30363d', text: '#e6edf3', muted: '#9aa4af', accent: '#58a6ff', success: '#3fb950', warning: '#d29922', error: '#f85149'},
    {id: 'night-owl', name: 'Night Owl', kind: 'dark', bg: '#011627', surface: '#0b2942', border: '#1d3b53', text: '#d6deeb', muted: '#b0c4d8', accent: '#82aaff', success: '#addb67', warning: '#ffcb8b', error: '#ef5350'},
    {id: 'github-light', name: 'GitHub Light', kind: 'light', bg: '#ffffff', surface: '#f6f8fa', border: '#d0d7de', text: '#1f2328', muted: '#59636e', accent: '#0969da', success: '#1a7f37', warning: '#9a6700', error: '#cf222e'},
    {id: 'solarized-light', name: 'Solarized Light', kind: 'light', bg: '#fdf6e3', surface: '#eee8d5', border: '#d3cbb7', text: '#073642', muted: '#3e5158', accent: '#0b5fad', success: '#4f5a00', warning: '#8a6a00', error: '#a33a12'}
  ];

  function byId(id) {
    for (var i = 0; i < THEMES.length; i++) if (THEMES[i].id === id) return THEMES[i];
    return THEMES[8];
  }

  function channel(hex, index) {
    var value = parseInt(hex.slice(index + 1, index + 3), 16) / 255;
    return value <= 0.03928 ? value / 12.92 : Math.pow((value + 0.055) / 1.055, 2.4);
  }

  function luminance(hex) {
    return 0.2126 * channel(hex, 0) + 0.7152 * channel(hex, 2) + 0.0722 * channel(hex, 4);
  }

  function ink(hex) {
    var level = luminance(hex);
    var onWhite = 1.05 / (level + 0.05);
    var onBlack = (level + 0.05) / 0.05;
    return onWhite >= onBlack ? '#ffffff' : '#0b0d10';
  }

  function savedId() {
    var stored = 'github-dark';
    try { stored = localStorage.getItem(KEY) || 'github-dark'; } catch (error) {}
    return byId(stored).id;
  }

  function paint(theme) {
    var root = document.documentElement;
    var light = theme.kind === 'light';
    var tokens = {
      '--bg': theme.bg,
      '--surface': theme.surface,
      '--border': theme.border,
      '--text': theme.text,
      '--muted': theme.muted,
      '--accent': theme.accent,
      '--accent-ink': ink(theme.accent),
      '--success': theme.success,
      '--success-ink': ink(theme.success),
      '--warning': theme.warning,
      '--warning-ink': ink(theme.warning),
      '--error': theme.error,
      '--error-ink': ink(theme.error),
      '--ink': theme.text,
      '--code-bg': light ? theme.surface : theme.bg,
      '--code-text': theme.text,
      '--tok-key': light ? theme.accent : '#c4b5fd',
      '--tok-str': light ? theme.success : '#86efac',
      '--tok-num': light ? theme.warning : '#fcd34d',
      '--tok-comment': theme.muted,
      '--rail': theme.surface,
      '--panel': theme.surface,
      '--panel2': theme.bg,
      '--line': theme.border,
      '--line2': theme.border,
      '--dim': theme.muted,
      '--mint': theme.accent,
      '--mintbg': theme.bg,
      '--amber': theme.warning,
      '--amberbg': theme.bg,
      '--blue': theme.accent
    };
    Object.keys(tokens).forEach(function (name) { root.style.setProperty(name, tokens[name]); });
    root.setAttribute('data-theme', theme.id);
    root.style.colorScheme = theme.kind;
  }

  function tellFrame(id) {
    var frame = document.getElementById('xp');
    if (!frame || !frame.contentWindow) return;
    var target = location.protocol === 'file:' ? '*' : location.origin;
    frame.contentWindow.postMessage({rt: 'theme', id: id}, target);
  }

  function apply(id) {
    var theme = byId(id);
    paint(theme);
    try { localStorage.setItem(KEY, theme.id); } catch (error) {}
    tellFrame(theme.id);
    return theme;
  }

  function preview(id) {
    var theme = byId(id);
    paint(theme);
    tellFrame(theme.id);
    return theme;
  }

  function restore() {
    var theme = byId(savedId());
    paint(theme);
    tellFrame(theme.id);
    return theme;
  }

  paint(byId(savedId()));
  window.RTTheme = {
    themes: THEMES,
    current: savedId,
    get: byId,
    apply: apply,
    preview: preview,
    restore: restore
  };

  window.addEventListener('message', function (event) {
    var message = event.data || {};
    if (message.rt === 'theme' && message.id) paint(byId(message.id));
  });
})();
