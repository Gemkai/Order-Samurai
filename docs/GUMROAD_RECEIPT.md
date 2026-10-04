# Gumroad receipt text (owner: paste into the product's receipt / "content" field)

Keep this in sync with `docs/ONBOARDING.md`. The buyer sees it on the receipt page and in
the receipt email, next to their license key.

**Do not paste this into Gumroad until the published `dist/order-samurai-core.zip` has been
rebuilt from a `main` that includes the one-command activation.** The curl installer
downloads that zip; an older zip installs without asking for the key.

---

**Thanks for buying Order Samurai Pro!**

Setup takes about two minutes on macOS or Linux. You need Python 3.11 or newer; the
installer tells you where to get it if it is missing.

1. Copy your license key above (use the Copy button).
2. Open Terminal and paste this one line:

   ```
   curl -fsSL https://raw.githubusercontent.com/Gemkai/order-samurai/main/install.sh | bash
   ```

3. When it asks for your license key, paste it and press Enter. Nothing appears while
   you paste; that is normal.
4. You should see: **Order Samurai Pro activated**. Done.

Already have Order Samurai installed? Run this and paste the key when asked:

```
python3 ~/.samurai/core/bin/samurai activate
```

Trouble? Run `python3 ~/.samurai/core/bin/samurai doctor` and email the output to
support@ordersamurai.ai.
14-day money-back guarantee: reply to this receipt to request a refund.
