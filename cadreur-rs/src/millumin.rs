//! Millumin OSC output over UDP.
//!
//! Ported from `src/cadreur/millumin.py`, minus its feedback listener and
//! correlated `query()`. That path is unreachable in practice: `query` is
//! called from nowhere in the Python, `probe_enabled` is passed to the engine
//! and never stored, and `millumin.feedback` defaults to false because custom
//! Interaction addresses — the configuration actually in use — do not answer
//! `/?` readback at all. Carrying it would have meant porting a pending-reply
//! correlation table that nothing can reach.
//!
//! Addresses are configured per channel (`/front/scale/1`,
//! `/front/positionV/1`, ...), so both custom Interaction bindings and the
//! standard `/layer:NAME` API are just data.
//!
//! Sends are deliberately synchronous. UDP never meaningfully blocks, and
//! keeping them sync keeps the engine tick synchronous and testable with a
//! fake sender, exactly as the Python did.

use std::net::{SocketAddr, ToSocketAddrs, UdpSocket};

use rosc::{OscMessage, OscPacket, OscType, encoder};

use crate::config::MilluminCfg;

/// What the engine needs. A test double implements this to capture sends.
pub trait OscSender: Send + Sync {
    /// One float to an explicit address.
    fn send_value(&self, address: &str, value: f64);
    /// An argument-less message — a pure path trigger, used to reveal a layer.
    fn send_bang(&self, address: &str);
}

pub struct MilluminIo {
    socket: Option<UdpSocket>,
    dest: Option<SocketAddr>,
}

impl MilluminIo {
    pub fn new(cfg: &MilluminCfg) -> Self {
        let dest = (cfg.host.as_str(), cfg.port)
            .to_socket_addrs()
            .ok()
            .and_then(|mut it| it.next());
        let socket = match UdpSocket::bind("0.0.0.0:0") {
            Ok(s) => Some(s),
            Err(e) => {
                eprintln!("Cannot open the OSC socket: {e} — Millumin will not be driven");
                None
            }
        };
        match dest {
            Some(d) => eprintln!("OSC out -> {d}"),
            None => eprintln!("Cannot resolve {}:{} — Millumin will not be driven", cfg.host, cfg.port),
        }
        Self { socket, dest }
    }

    fn send(&self, address: &str, args: Vec<OscType>) {
        if address.is_empty() {
            return;
        }
        let (Some(socket), Some(dest)) = (&self.socket, self.dest) else { return };
        let packet = OscPacket::Message(OscMessage { addr: address.to_string(), args });
        let Ok(buf) = encoder::encode(&packet) else {
            eprintln!("Cannot encode OSC for {address}");
            return;
        };
        if let Err(e) = socket.send_to(&buf, dest) {
            eprintln!("OSC send failed: {e}");
        }
    }
}

impl OscSender for MilluminIo {
    fn send_value(&self, address: &str, value: f64) {
        // python-osc sends Python floats as OSC float32; keep the wire format
        // identical so Millumin sees no difference.
        self.send(address, vec![OscType::Float(value as f32)]);
    }

    fn send_bang(&self, address: &str) {
        self.send(address, vec![]);
    }
}

/// Captures sends so engine tests can assert on OSC traffic.
#[cfg(test)]
#[derive(Default)]
pub struct FakeSender {
    pub sent: std::sync::Mutex<Vec<(String, Option<f64>)>>,
}

#[cfg(test)]
impl FakeSender {
    /// Every value sent to one address, in order.
    pub fn values_to(&self, address: &str) -> Vec<f64> {
        self.sent
            .lock()
            .expect("lock")
            .iter()
            .filter(|(a, v)| a == address && v.is_some())
            .map(|(_, v)| v.expect("filtered"))
            .collect()
    }

    pub fn any_to(&self, addresses: &[&str]) -> bool {
        self.sent.lock().expect("lock").iter().any(|(a, _)| addresses.contains(&a.as_str()))
    }

    pub fn clear(&self) {
        self.sent.lock().expect("lock").clear();
    }

    pub fn is_empty(&self) -> bool {
        self.sent.lock().expect("lock").is_empty()
    }
}

#[cfg(test)]
impl OscSender for FakeSender {
    fn send_value(&self, address: &str, value: f64) {
        self.sent.lock().expect("lock").push((address.to_string(), Some(value)));
    }

    fn send_bang(&self, address: &str) {
        self.sent.lock().expect("lock").push((address.to_string(), None));
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn encodes_a_float_message_like_python_osc() {
        let packet = OscPacket::Message(OscMessage {
            addr: "/front/scale/1".into(),
            args: vec![OscType::Float(0.5)],
        });
        let buf = encoder::encode(&packet).expect("encodes");
        // address padded to a 4-byte boundary, then the ",f" type tag
        assert!(buf.starts_with(b"/front/scale/1\0\0"));
        assert!(buf.windows(2).any(|w| w == b",f"));
    }

    #[test]
    fn a_bang_carries_no_arguments() {
        let packet =
            OscPacket::Message(OscMessage { addr: "/front/layer/1".into(), args: vec![] });
        let buf = encoder::encode(&packet).expect("encodes");
        assert!(buf.windows(1).any(|w| w == b","));
        // no 'f' type tag: the trigger is the address alone
        let tags_start = buf.iter().position(|&b| b == b',').expect("type tag");
        assert_eq!(buf[tags_start + 1], 0, "expected an empty type tag list");
    }

    #[test]
    fn empty_address_is_ignored() {
        let io = MilluminIo { socket: None, dest: None };
        io.send_value("", 1.0); // must not panic
        io.send_bang("");
    }

    #[test]
    fn fake_sender_records_both_kinds() {
        let f = FakeSender::default();
        f.send_value("/a", 0.25);
        f.send_bang("/b");
        let sent = f.sent.lock().expect("lock");
        assert_eq!(sent[0], ("/a".to_string(), Some(0.25)));
        assert_eq!(sent[1], ("/b".to_string(), None));
    }
}
