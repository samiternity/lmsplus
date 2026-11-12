/* Optional logged-in usage tracking. Analytics must never block app behavior. */
(function () {
    'use strict';
    try {
        const node = document.getElementById('usage-analytics-config');
        if (!node || window.unitrackAnalyticsStarted) return;
        const config = JSON.parse(node.textContent);
        window.unitrackAnalyticsStarted = true;
        window.dataLayer = window.dataLayer || [];
        window.gtag = function () { window.dataLayer.push(arguments); };

        // Set safe defaults before initialization, including automatic lifecycle
        // events. Never let the tag read the page's real URL, title or referrer.
        const page = {
            page_location: config.page_location,
            page_title: config.page_title,
            page_referrer: config.page_referrer
        };
        window.gtag('set', page);
        window.gtag('js', new Date());
        window.gtag('config', config.measurement_id, {
            ...page,
            user_id: config.user_id,
            send_page_view: false,
            allow_google_signals: false,
            allow_ad_personalization_signals: false
        });
        window.gtag('event', 'page_view', {...page, send_to: config.measurement_id});

        const script = document.createElement('script');
        script.async = true;
        script.referrerPolicy = 'no-referrer';
        script.src = 'https://www.googletagmanager.com/gtag/js?id=' +
            encodeURIComponent(config.measurement_id);
        document.head.appendChild(script);
    } catch (_) {
        // Missing, blocked or malformed analytics must not affect the app.
    }
}());
