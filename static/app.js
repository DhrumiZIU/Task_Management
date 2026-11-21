(() => {
  const body = document.body;
  const feedUrl = body.dataset.feedUrl;
  const pollSeconds = Number(body.dataset.pollSeconds || 5);
  let latestId = Number(body.dataset.lastActivity || 0);
  const toastStack = document.querySelector("#toast-stack");

  function showToast(item) {
    if (!toastStack) return;
    const toast = document.createElement("div");
    toast.className = "toast new";
    const title = document.createElement("strong");
    title.textContent = `${item.actor}: ${item.event}`;
    const detail = document.createElement("small");
    detail.textContent = [item.member, item.task, item.route].filter(Boolean).join(" · ");
    toast.append(title, detail);
    toastStack.prepend(toast);
    window.setTimeout(() => toast.remove(), 9000);
  }

  function addFeedItem(item) {
    const feed = document.querySelector("#activity-feed");
    if (!feed) return;
    const empty = feed.querySelector(".empty");
    if (empty) empty.remove();
    if (feed.querySelector(`[data-activity-id="${item.id}"]`)) return;
    const entry = document.createElement("div");
    entry.className = "activity-item";
    entry.dataset.activityId = item.id;
    const strong = document.createElement("strong");
    strong.textContent = item.actor;
    entry.append(strong, document.createTextNode(` ${item.event.toLowerCase()}`));
    const details = document.createElement("small");
    details.textContent = [item.member, item.task].filter(Boolean).join(" · ");
    const lineBreak = document.createElement("br");
    const route = document.createTextNode(`${item.time} · ${item.route}`);
    details.append(lineBreak, route);
    entry.append(details);
    feed.prepend(entry);
  }

  function updateSummary(summary) {
    Object.entries(summary || {}).forEach(([name, value]) => {
      document.querySelectorAll(`[data-live="${name}"]`).forEach((node) => { node.textContent = value; });
    });
  }

  async function checkFeed() {
    if (!feedUrl) return;
    try {
      const response = await fetch(`${feedUrl}?after=${latestId}`, { headers: { Accept: "application/json" }, cache: "no-store" });
      if (!response.ok) return;
      const payload = await response.json();
      updateSummary(payload.summary);
      const activities = payload.activities || [];
      activities.slice().reverse().forEach((item) => {
        addFeedItem(item);
        showToast(item);
        if (document.hidden && "Notification" in window && Notification.permission === "granted") {
          new Notification("Team Planner update", { body: `${item.actor}: ${item.event} — ${item.task || item.member}` });
        }
      });
      latestId = Math.max(latestId, Number(payload.latest_id || 0));
    } catch (_) {
      // A temporarily unavailable local server should not interrupt the page.
    }
  }

  const browserButton = document.querySelector("#browser-notifications");
  if (!("Notification" in window)) {
    if (browserButton) browserButton.hidden = true;
  } else if (Notification.permission === "granted") {
    if (browserButton) browserButton.textContent = "Browser alerts enabled";
  }
  if (browserButton) {
    browserButton.addEventListener("click", async () => {
      const permission = await Notification.requestPermission();
      browserButton.textContent = permission === "granted" ? "Browser alerts enabled" : "Browser alerts blocked";
    });
  }
  window.setInterval(checkFeed, Math.max(3, pollSeconds) * 1000);
})();
