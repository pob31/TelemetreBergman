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

use tao::event::{Event, WindowEvent};
use tao::event_loop::{ControlFlow, EventLoopBuilder};
use tao::window::WindowBuilder;
use wry::WebViewBuilder;
use wry::dpi::{LogicalSize, Size};

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
            eprintln!("Cannot open the window: {e}");
            eprintln!("Run with --headless and open {url} in a browser instead.");
            return;
        }
    };

    let webview = WebViewBuilder::new()
        .with_url(url)
        // Matches the UI's own dark ground, so the window never flashes white
        // while the first paint is on its way.
        .with_background_color((11, 15, 20, 255))
        .build(&window);

    let _webview = match webview {
        Ok(w) => w,
        Err(e) => {
            eprintln!("Cannot create the web view: {e}");
            eprintln!("Run with --headless and open {url} in a browser instead.");
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
