# Connecting Schwab (end users)

Give this to customers who use your hosted dashboard. Operators: the same content ships as a built-in page at **`/static/connect-schwab-guide.html`** (linked from the dashboard as **Schwab setup guide** when Schwab OAuth is enabled and `WEB_IMPLEMENTATION_GUIDE_URL` is not set). To use your own help center URL instead, set `WEB_IMPLEMENTATION_GUIDE_URL` to an `https://` link.

## Steps

1. Sign in to **your** product (this site)—not the Schwab developer portal.
2. Open **Setup** / **Connect Schwab & setup**.
3. Click **Connect Schwab (account)** and complete Schwab’s approval; you should return here with a success message.
4. Click **Connect Schwab (market)** and complete the second approval.
5. Optionally run **Start Wizard** and steps 1–4 to confirm health, scan, and paper test (subscription rules may apply).

## Schwab Developer Portal URLs (self-hosted)

If you maintain your own Schwab developer apps, sign in first and open:

- `GET /api/oauth/schwab/portal-config`

Use these values from the response:

- `account_callback_url` for the Schwab **account** app callback
- `market_callback_url` for the Schwab **market** app callback
- `frontend_return_url` as the return/home URL (when Schwab asks for it)

## Why two buttons?

Schwab’s APIs separate **brokerage account** access from **market data** access. This app needs both for scans and trading features.

## Live trading

Linking Schwab does not by itself send live orders. If your deployment uses a live-trading gate, users complete a separate in-app confirmation.

## Legal

Point users to **`/static/legal.html`** (or your counsel-approved equivalent). This document is not legal advice.
