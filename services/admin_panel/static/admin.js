"use strict";

function showToast(message, failed = false) {
  const toast = document.getElementById("toast");
  if (!toast) return;
  toast.textContent = message;
  toast.className = failed ? "show failed" : "show";
  window.setTimeout(() => { toast.className = ""; }, 3000);
}

document.addEventListener("click", async (event) => {
  const button = event.target.closest("[data-action]");
  if (!button) return;
  event.preventDefault();

  const payload = {};
  if (button.dataset.danger && button.dataset.armed !== "true") {
    button.dataset.armed = "true";
    button.dataset.originalText = button.textContent;
    button.textContent = "Confirm pause";
    window.setTimeout(() => {
      if (button.dataset.armed === "true") {
        button.dataset.armed = "false";
        button.textContent = button.dataset.originalText;
      }
    }, 6000);
    return;
  }
  if (button.dataset.danger) payload.confirm = "PAUSE";
  if (button.dataset.reason) {
    const reason = window.prompt("Reason for rejection:", "TXID did not pass payment verification");
    if (reason === null) return;
    payload.reason = reason;
  }

  button.disabled = true;
  try {
    const response = await fetch(button.dataset.action, {
      method: "POST",
      credentials: "same-origin",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify(payload),
    });
    if (!response.ok) throw new Error("Action failed");
    showToast("Action completed");
    window.setTimeout(() => window.location.reload(), 350);
  } catch (_error) {
    showToast("The action could not be completed", true);
    button.disabled = false;
  }
});

document.addEventListener("DOMContentLoaded", () => {
  const canvas = document.getElementById("pnl-chart");
  const source = document.getElementById("pnl-data");
  if (!canvas || !source) return;
  const points = JSON.parse(source.textContent || "[]");
  const context = canvas.getContext("2d");
  const ratio = window.devicePixelRatio || 1;
  const width = canvas.clientWidth;
  const height = Number(canvas.getAttribute("height"));
  canvas.width = width * ratio;
  canvas.height = height * ratio;
  context.scale(ratio, ratio);
  context.clearRect(0, 0, width, height);
  if (points.length === 0) {
    context.fillStyle = "#738097";
    context.font = "13px system-ui";
    context.fillText("No closed trades in this range", 20, height / 2);
    return;
  }
  const values = points.map((point) => point.value);
  const minimum = Math.min(0, ...values);
  const maximum = Math.max(0, ...values);
  const spread = maximum - minimum || 1;
  const x = (index) => 20 + index * ((width - 40) / Math.max(1, points.length - 1));
  const y = (value) => 12 + (maximum - value) * ((height - 32) / spread);
  context.strokeStyle = "#2f7df6";
  context.lineWidth = 2;
  context.beginPath();
  points.forEach((point, index) => index ? context.lineTo(x(index), y(point.value)) : context.moveTo(x(index), y(point.value)));
  context.stroke();
});
