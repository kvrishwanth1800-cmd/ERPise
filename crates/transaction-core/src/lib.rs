#![forbid(unsafe_code)]

/// Returns true when an idempotency key contains at least one visible character.
pub fn is_valid_idempotency_key(value: &str) -> bool {
    !value.trim().is_empty()
}

#[cfg(test)]
mod tests {
    use super::is_valid_idempotency_key;

    #[test]
    fn accepts_a_visible_idempotency_key() {
        assert!(is_valid_idempotency_key("sale-123"));
    }

    #[test]
    fn rejects_a_blank_idempotency_key() {
        assert!(!is_valid_idempotency_key(" \t "));
    }
}
