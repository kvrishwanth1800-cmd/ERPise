# WO-26 Storefront Checkout Evidence

## Coverage

- Product presentation exposes product name, price, and stock availability.
- Checkout uses the caller's idempotency key and reuses an in-flight or recovered result for retries.
- The checkout integration delegates reservation commitment to one shared checkout port. The final-unit race test proves that its two competing requests produce at most one completed outcome.
- Product presentation returns labelled content for a browser layer to render as its heading, price, and availability status. Responsive layout and keyboard handling remain browser responsibilities because this repository has no browser application or UI runtime.

## Verification

Run `pnpm test` for storefront integration tests. The repository quality workflow also runs TypeScript checks and all Vitest tests.
