/**
 * Demo station sealing — the browser half of the recording bypass.
 *
 * Loaded first, from <head>, with no defer, so it runs before viewer.js and
 * before any other script has had a chance to open a transport. It does two
 * things, and only on /demo:
 *
 *   1. Stamps every outgoing request with the header the server reads to bind
 *      that request to its null recorder. This is the client-side twin of
 *      app/recording.py's injection point: a fetch somebody adds next month
 *      inherits the tag because it goes through window.fetch, the same way a
 *      new server call site inherits the null sink because it goes through
 *      recording.current(). Nobody has to remember.
 *
 *   2. Takes away the browser's own persistence. localStorage, sessionStorage,
 *      document.cookie and IndexedDB are replaced with in-memory equivalents or
 *      refused outright, so nothing this page does survives the tab closing --
 *      and, incidentally, so two tabs of the demo in one browser cannot reach
 *      each other's settings through a shared storage key.
 *
 * Then it sets window.__CAD_DEMO_SEALED__. viewer.js checks that flag on /demo
 * and refuses to start without it. That is the point of the flag: if this file
 * fails to load, the demo does not run in an unsealed state -- it does not run.
 *
 * What is deliberately left working: everything the viewer needs in memory, and
 * genuine crash reporting through console.error, which carries no interaction
 * content of its own.
 */
(function () {
    'use strict';

    var isDemo = window.location.pathname.replace(/\/+$/, '') === '/demo';
    window.CAD_DEMO_MODE = isDemo;
    if (!isDemo) return;

    var DEMO_HEADER = 'X-CAD-Demo';

    // -- 1. Network ---------------------------------------------------------

    var nativeFetch = window.fetch ? window.fetch.bind(window) : null;
    if (nativeFetch) {
        window.fetch = function (input, init) {
            var options = Object.assign({}, init || {});
            var headers = new Headers(
                (init && init.headers) ||
                (input instanceof Request ? input.headers : undefined)
            );
            headers.set(DEMO_HEADER, '1');
            options.headers = headers;
            // A Request object carries its own headers, which the options above
            // would not override. Rebuild it so the tag survives either form.
            if (input instanceof Request) {
                return nativeFetch(new Request(input, { headers: headers }), options);
            }
            return nativeFetch(input, options);
        };
    }

    if (window.XMLHttpRequest) {
        var nativeSend = window.XMLHttpRequest.prototype.send;
        window.XMLHttpRequest.prototype.send = function () {
            try { this.setRequestHeader(DEMO_HEADER, '1'); } catch (_) {}
            return nativeSend.apply(this, arguments);
        };
    }

    // sendBeacon cannot carry a header, so it cannot be tagged, so it is refused.
    // Returning true keeps a caller from falling back to a transport that would
    // then go untagged. Nothing in this app uses it today; this is here so that
    // if something starts to, it fails closed rather than quietly reporting.
    if (window.navigator && typeof window.navigator.sendBeacon === 'function') {
        window.navigator.sendBeacon = function () { return true; };
    }

    // -- 2. Persistence -----------------------------------------------------

    // A Storage-shaped object that forgets everything when the tab does.
    function memoryStorage() {
        var data = Object.create(null);
        return {
            get length() { return Object.keys(data).length; },
            key: function (i) { return Object.keys(data)[i] !== undefined ? Object.keys(data)[i] : null; },
            getItem: function (k) { return Object.prototype.hasOwnProperty.call(data, String(k)) ? data[String(k)] : null; },
            setItem: function (k, v) { data[String(k)] = String(v); },
            removeItem: function (k) { delete data[String(k)]; },
            clear: function () { data = Object.create(null); },
        };
    }

    ['localStorage', 'sessionStorage'].forEach(function (name) {
        try {
            Object.defineProperty(window, name, {
                configurable: true,
                get: (function () {
                    var store = memoryStorage();
                    return function () { return store; };
                })(),
            });
        } catch (_) {
            // Some engines refuse to redefine these. Best effort: empty what is
            // there so nothing from a previous visit is read back, and carry on.
            try { window[name].clear(); } catch (__) {}
        }
    });

    // Cookies: reads return nothing, writes are dropped. The server sets none on
    // this path either (the null recorder's attach_session_cookie is a no-op);
    // this closes the browser side of the same door.
    try {
        Object.defineProperty(document, 'cookie', {
            configurable: true,
            get: function () { return ''; },
            set: function () { return ''; },
        });
    } catch (_) {}

    // IndexedDB is unused today. Refused rather than left available, so it stays
    // unused by accident as well as by intent.
    try {
        Object.defineProperty(window, 'indexedDB', {
            configurable: true,
            get: function () { return undefined; },
        });
    } catch (_) {}

    // -- 3. Seal ------------------------------------------------------------

    window.__CAD_DEMO_SEALED__ = true;
})();
