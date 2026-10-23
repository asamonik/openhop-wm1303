"use strict";

const views = {
  status: ["Hardware health and packet activity at a glance.", "WM1303 Manager status screen showing hardware health, received and transmitted packets, and packet activity."],
  channels: ["Configure the radio settings for each channel.", "WM1303 Manager channel configuration screen."],
  "bridge-rules": ["Choose how packets are forwarded between channels.", "WM1303 Manager bridge rules screen showing channel forwarding settings."],
  spectrum: ["Explore radio activity with the spectrum scanner.", "WM1303 Manager spectrum screen showing a frequency scan and signal levels."],
};

const previewButtons = document.querySelector(".preview-buttons");
previewButtons.hidden = false;
previewButtons.addEventListener("click", (event) => {
  const button = event.target.closest("button[data-view]");
  if (!button) return;
  const key = button.dataset.view;
  const [caption, alt] = views[key];
  for (const item of previewButtons.querySelectorAll("button")) {
    item.setAttribute("aria-pressed", String(item === button));
  }
  const image = document.getElementById("preview-image");
  image.src = `assets/${key}.png`;
  image.alt = alt;
  document.getElementById("preview-link").href = image.getAttribute("src");
  document.getElementById("preview-caption").textContent = caption;
});

const copyButton = document.getElementById("copy-command");
copyButton.hidden = false;
copyButton.addEventListener("click", async () => {
  const command = document.getElementById("install-command");
  const status = document.getElementById("copy-status");
  try {
    await navigator.clipboard.writeText(command.textContent.trim());
    status.textContent = "Command copied to clipboard.";
    copyButton.textContent = "Copied ✓";
  } catch {
    const selection = window.getSelection();
    const range = document.createRange();
    range.selectNodeContents(command);
    selection.removeAllRanges();
    selection.addRange(range);
    status.textContent = "Copy is unavailable in this browser. The command is selected; copy it manually.";
  }
});
