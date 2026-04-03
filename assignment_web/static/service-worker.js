self.addEventListener("push", (event) => {
    const payload = event.data ? event.data.json() : {};
    const title = payload.title || "New update";
    const options = {
        body: payload.body || "You have a new notification.",
        data: {
            url: payload.url || "/",
        },
    };

    event.waitUntil(self.registration.showNotification(title, options));
});

self.addEventListener("notificationclick", (event) => {
    event.notification.close();
    const targetUrl = event.notification.data?.url || "/";
    event.waitUntil(clients.openWindow(targetUrl));
});
