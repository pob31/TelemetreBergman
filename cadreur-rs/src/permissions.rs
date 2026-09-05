//! macOS permission awareness.
//!
//! Sequoia gates local-network access behind a prompt that appears **once**.
//! If the operator dismisses it, the Pi becomes permanently unreachable and
//! the interface just says "Pi hors ligne" — indistinguishable from a cable
//! problem. Nothing here can grant the permission; what it does is make the
//! prompt fire predictably at first launch and make a refusal legible
//! afterwards, instead of leaving the operator debugging the wrong thing.

use std::net::IpAddr;

use crate::config::Config;

/// The host part of a URL, without a URL-parsing dependency.
pub fn host_of_url(url: &str) -> Option<String> {
    let rest = url.split("://").nth(1).unwrap_or(url);
    let host = rest.split(['/', '?', '#']).next()?;
    // strip credentials and port, tolerate a bracketed IPv6 literal
    let host = host.rsplit('@').next()?;
    if let Some(end) = host.strip_prefix('[').and_then(|h| h.find(']').map(|i| &h[..i])) {
        return Some(end.to_string());
    }
    let host = host.split(':').next()?;
    (!host.is_empty()).then(|| host.to_string())
}

pub fn is_loopback(host: &str) -> bool {
    if host.eq_ignore_ascii_case("localhost") {
        return true;
    }
    host.parse::<IpAddr>().is_ok_and(|ip| ip.is_loopback())
}

/// True when reaching this host needs the macOS "Local Network" permission.
///
/// Loopback is exempt, and a public internet address is a different gate
/// entirely — only genuine local-network traffic triggers the prompt.
pub fn needs_local_network(host: &str) -> bool {
    if is_loopback(host) {
        return false;
    }
    match host.parse::<IpAddr>() {
        Ok(IpAddr::V4(ip)) => ip.is_private() || ip.is_link_local(),
        Ok(IpAddr::V6(ip)) => {
            // unique-local (fc00::/7) and link-local (fe80::/10)
            let s = ip.segments()[0];
            (s & 0xfe00) == 0xfc00 || (s & 0xffc0) == 0xfe80
        }
        // A bare hostname on the stage LAN resolves locally; assume it counts.
        Err(_) => true,
    }
}

/// What macOS will ask for on this configuration, in the operator's language.
pub fn startup_notes(cfg: &Config) -> Vec<String> {
    let mut notes = Vec::new();

    if let Some(h) = host_of_url(&cfg.telemetre.url)
        && needs_local_network(&h)
    {
        notes.push(format!(
            "Réseau local : Cadreur doit joindre le télémètre ({h}). \
             macOS le demande au premier lancement — répondre « Autoriser »."
        ));
    }
    if needs_local_network(&cfg.millumin.host) {
        notes.push(format!(
            "Réseau local : Millumin est sur une autre machine ({}). \
             Même autorisation.",
            cfg.millumin.host
        ));
    }
    if cfg.web.host != "127.0.0.1" && cfg.web.host != "localhost" {
        notes.push(format!(
            "Connexions entrantes : l'interface écoute sur {} (accès tablette). \
             Le pare-feu demandera d'accepter — répondre « Autoriser ».",
            cfg.web.host
        ));
    }
    notes
}

/// Shown when the telemetre has never connected and the address is on the LAN.
pub fn local_network_hint(host: &str) -> String {
    format!(
        "Cadreur n'arrive pas à joindre le télémètre ({host}).\n\n\
         Si le boîtier est allumé et sur le même réseau, c'est probablement \
         l'autorisation « Réseau local » qui manque :\n\n\
         Réglages Système → Confidentialité et sécurité → Réseau local → \
         activer Cadreur.\n\n\
         Puis quitter Cadreur (⌘Q) et le relancer : les autorisations ne \
         s'appliquent qu'au démarrage."
    )
}

/// Best-effort native alert. Never blocks the caller and never fails the app —
/// if osascript is unavailable the message has already gone to the log.
pub fn alert(title: &str, message: &str) {
    let script = format!(
        "display dialog {} buttons {{\"OK\"}} default button 1 with title {} with icon caution",
        applescript_string(message),
        applescript_string(title)
    );
    let _ = std::process::Command::new("/usr/bin/osascript").arg("-e").arg(script).spawn();
}

fn applescript_string(s: &str) -> String {
    let escaped = s.replace('\\', "\\\\").replace('"', "\\\"").replace('\n', "\\n");
    format!("\"{escaped}\"")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn extracts_the_host_from_a_url() {
        assert_eq!(host_of_url("http://192.168.0.51").as_deref(), Some("192.168.0.51"));
        assert_eq!(host_of_url("http://192.168.0.51/").as_deref(), Some("192.168.0.51"));
        assert_eq!(host_of_url("http://192.168.0.51:8080/stream").as_deref(), Some("192.168.0.51"));
        assert_eq!(host_of_url("http://pi.local/x").as_deref(), Some("pi.local"));
        assert_eq!(host_of_url("http://[fe80::1]:80/").as_deref(), Some("fe80::1"));
        assert_eq!(host_of_url("192.168.1.5").as_deref(), Some("192.168.1.5"));
    }

    #[test]
    fn loopback_is_exempt() {
        for h in ["127.0.0.1", "localhost", "LOCALHOST", "::1"] {
            assert!(is_loopback(h), "{h} should be loopback");
            assert!(!needs_local_network(h), "{h} must not need permission");
        }
    }

    #[test]
    fn lan_addresses_need_the_permission() {
        for h in ["192.168.0.51", "10.0.0.4", "172.16.3.9", "169.254.1.1", "pi.local"] {
            assert!(needs_local_network(h), "{h} should need Local Network");
        }
    }

    #[test]
    fn a_public_address_is_a_different_gate() {
        assert!(!needs_local_network("93.184.216.34"));
    }

    #[test]
    fn default_config_warns_about_the_pi_only() {
        let notes = startup_notes(&Config::default());
        assert_eq!(notes.len(), 1, "{notes:?}");
        assert!(notes[0].contains("Réseau local"));
        assert!(notes[0].contains("192.168.1.36"));
    }

    #[test]
    fn tablet_access_adds_the_firewall_note() {
        let mut cfg = Config::default();
        cfg.web.host = "0.0.0.0".into();
        let notes = startup_notes(&cfg);
        assert_eq!(notes.len(), 2, "{notes:?}");
        assert!(notes.iter().any(|n| n.contains("Connexions entrantes")));
    }

    #[test]
    fn loopback_only_config_asks_for_nothing() {
        let mut cfg = Config::default();
        cfg.telemetre.url = "http://127.0.0.1:9000".into();
        assert!(startup_notes(&cfg).is_empty());
    }

    #[test]
    fn applescript_strings_are_escaped() {
        assert_eq!(applescript_string("a\"b"), "\"a\\\"b\"");
        assert_eq!(applescript_string("a\nb"), "\"a\\nb\"");
        assert_eq!(applescript_string("a\\b"), "\"a\\\\b\"");
    }
}
