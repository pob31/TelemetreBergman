//! Native window: the same web UI in a macOS WKWebView, via wry.
//!
//! Replaces `src/cadreur/gui.py`. Closing the window shuts the process down,
//! as it did before.
//!
//! One behaviour is deliberately NOT carried over. The Python probed the port
//! first and, if anything answered, skipped starting its own server and opened
//! a window on whatever was there — so an unrelated program holding port 8080
//! produced a blank window with no error. Here the server binds before the
//! window opens, and a bind failure exits with a message naming the port. A
//! stranger's web server can no longer be mistaken for Cadreur.
//!
//! The window also forwards JavaScript errors into the log. A `.app` launched
//! from the Finder has no console anyone can reach, so a UI control that threw
//! looked exactly like a control that did nothing — which is how the dead
//! `prompt()`/`confirm()` calls survived a release. Anything the page throws
//! now lands in `~/Library/Logs/Cadreur/cadreur.log`, where
//! `scripts/diagnose_mac.sh` reads it.

use tao::event::{Event, WindowEvent};
use tao::event_loop::{ControlFlow, EventLoopBuilder};
use tao::window::WindowBuilder;
use wry::WebViewBuilder;
use wry::dpi::{LogicalSize, Size};

use crate::log_line;

/// Injected before the page runs. Reports uncaught errors, rejected promises
/// and console.error to the host, which writes them to the log file.
const ERROR_REPORTER: &str = r#"
(function () {
  var send = function (kind, text) {
    try { window.ipc.postMessage("js:" + kind + ": " + text); } catch (e) {}
  };
  window.addEventListener("error", function (e) {
    send("error", (e.message || "?") + " @ " + (e.filename || "?") + ":" + (e.lineno || 0));
  });
  window.addEventListener("unhandledrejection", function (e) {
    var r = e.reason;
    send("promise", (r && (r.stack || r.message)) || String(r));
  });
  var realError = console.error;
  console.error = function () {
    send("console", Array.prototype.map.call(arguments, String).join(" "));
    realError.apply(console, arguments);
  };
})();
"#;

pub fn run(url: &str) {
    let event_loop = EventLoopBuilder::new().build();
    let window = match WindowBuilder::new()
        .with_title("Cadreur Bergman")
        .with_inner_size(Size::Logical(LogicalSize::new(1180.0, 1100.0)))
        .with_min_inner_size(Size::Logical(LogicalSize::new(900.0, 700.0)))
        .build(&event_loop)
    {
        Ok(w) => w,
        Err(e) => {
            crate::log_line!("Cannot open the window: {e}");
            crate::log_line!("Run with --headless and open {url} in a browser instead.");
            return;
        }
    };

    let webview = WebViewBuilder::new()
        .with_url(url)
        .with_initialization_script(ERROR_REPORTER)
        .with_ipc_handler(|req| {
            let body = req.body();
            if let Some(msg) = body.strip_prefix("js:") {
                log_line!("UI: {msg}");
            }
        })
        // Matches the UI's own dark ground, so the window never flashes white
        // while the first paint is on its way.
        .with_background_color((11, 15, 20, 255))
        .build(&window);

    let _webview = match webview {
        Ok(w) => w,
        Err(e) => {
            crate::log_line!("Cannot create the web view: {e}");
            crate::log_line!("Run with --headless and open {url} in a browser instead.");
            return;
        }
    };

    event_loop.run(move |event, _, control_flow| {
        *control_flow = ControlFlow::Wait;
        if let Event::WindowEvent { event: WindowEvent::CloseRequested, .. } = event {
            *control_flow = ControlFlow::Exit;
        }
    });
}
