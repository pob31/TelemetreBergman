//! Native window. Placeholder until the wry implementation lands.

pub fn run(url: &str) {
    eprintln!("No window built in yet — open {url} in a browser, or pass --headless.");
    loop {
        std::thread::sleep(std::time::Duration::from_secs(3600));
    }
}
