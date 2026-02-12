# Google Sheets logging – detailed setup

This guide walks you through enabling signal logging to a Google Sheet (for both local and Railway).

---

## Part A: Google Cloud – service account and API

### A1. Open Google Cloud Console

1. Go to **https://console.cloud.google.com/**
2. Sign in with the Google account you use for Sheets.

### A2. Create or select a project

1. At the top, click the **project dropdown** (says “Select a project” or your project name).
2. Click **New Project**.
3. Name it (e.g. `SPX Signal Logging`), leave org as “No organization” if you prefer, click **Create**.
4. Wait for the project to be created, then **select that project** in the dropdown.

### A3. Enable Google Sheets API

1. In the left menu: **APIs & Services** → **Library** (or go to https://console.cloud.google.com/apis/library).
2. Search for **Google Sheets API**.
3. Click it, then click **Enable**.

### A4. Create a service account

1. **APIs & Services** → **Credentials**.
2. Click **+ Create Credentials** → **Service account**.
3. **Service account name:** e.g. `spx-signal-logger`.
4. Click **Create and Continue** (optional steps can be skipped with **Continue** then **Done**).

### A5. Create and download the JSON key

1. On the **Credentials** page, under **Service accounts**, click the service account you just created.
2. Open the **Keys** tab.
3. **Add key** → **Create new key** → choose **JSON** → **Create**.
4. A JSON file will download. **Keep this file private** (do not commit it). You’ll use its contents in the next parts.

---

## Part B: Create the spreadsheet and share it

### B1. Create the sheet

1. Go to **https://sheets.google.com** and create a **new blank spreadsheet**.
2. (Optional) Rename it, e.g. “SPX Signal Log”.
3. Copy the **spreadsheet ID** from the URL:
   - URL looks like: `https://docs.google.com/spreadsheets/d/1ABC...xyz123/edit`
   - The **spreadsheet ID** is the part between `/d/` and `/edit`: `1ABC...xyz123`

### B2. Get the service account email

1. Open the **downloaded JSON file** from A5.
2. Find the line `"client_email": "something@something.iam.gserviceaccount.com"`.
3. Copy that full email (e.g. `spx-signal-logger@my-project.iam.gserviceaccount.com`).

### B3. Share the sheet with the service account

1. In your Google Sheet, click **Share**.
2. In “Add people and groups”, paste the **service account email**.
3. Set permission to **Editor**.
4. **Uncheck** “Notify people” (the service account doesn’t read email).
5. Click **Share**.

The app will append rows to the **first worksheet** (Sheet1) in this spreadsheet. No need to create headers; the app adds them on first run.

---

## Part C: Prepare the credentials JSON (one line for config)

The app expects the **entire** service account JSON as a **single line** string. You’ll use this in both `.config` (local) and Railway (remote).

### Option 1: Minify with Python (recommended)

In a terminal (from any folder):

```bash
python -c "import json; f=open(r'C:\path\to\your\downloaded-key.json'); d=json.load(f); print(json.dumps(d, separators=(',',':')))"
```

- Replace `C:\path\to\your\downloaded-key.json` with the **actual path** to your downloaded JSON file.
- The output is one line. Copy the **entire** line (from `{` to `}`).

**Important:** The `private_key` inside the JSON contains real newlines. The command above keeps them as `\n` in the string, which is correct. When you paste into `.config` or Railway, paste the whole line as-is.

### Option 2: Minify manually

1. Open the JSON file in a text editor.
2. Remove all line breaks so the whole file is one line.
3. Inside `"private_key"`, the key has real newlines. Replace each newline with the two characters backslash and n: `\n` (so the key looks like `"-----BEGIN PRIVATE KEY-----\nMIIE...\n-----END PRIVATE KEY-----\n"`).
4. Copy that single line.

### Option 3: Use an online minifier

1. Go to a JSON minifier (e.g. https://www.minifier.org/ or search “json minify”).
2. Paste the full contents of your JSON file, then minify.
3. Copy the result (one line). Ensure the `private_key` value still contains `\n` where there were newlines (some tools keep them).

---

## Part D: Configure locally (.config)

1. Open (or create) your **`.config`** file in the **project root** (same folder as `app.py`).

2. Add or edit the `[GOOGLE_SHEETS]` section so it looks like this (use your real values):

```ini
[GOOGLE_SHEETS]
GOOGLE_SHEET_ID=1ABC...xyz123
GOOGLE_CREDENTIALS_JSON={"type":"service_account","project_id":"my-project",...}
```

- **GOOGLE_SHEET_ID:** paste the spreadsheet ID from B1 (no quotes, no spaces).
- **GOOGLE_CREDENTIALS_JSON:** paste the **entire** one-line JSON from Part C. No quotes around it in the `.config` file.

**If your JSON contains double quotes inside:** ConfigParser can handle values with `=`, `,`, and `"`. If you have issues, put the JSON in a separate file (e.g. `secrets/google_sa.json`) and in `.config` you could reference it—but the app is built to use the JSON string only, so prefer pasting the one-line JSON. If the line is very long and your editor or parser complains, use the Python one-liner in Part C and paste the output.

3. Save `.config`. Do **not** commit `.config` (it’s in `.gitignore`).

4. Restart your local app and trigger a signal once (e.g. open `http://127.0.0.1:5000/option_alpha_trigger`). Then open the Google Sheet and confirm a new row appeared (and that the first row is the header if the sheet was empty).

---

## Part E: Configure on Railway

1. Open your **Railway** project → select the **service** that runs this app.

2. Go to the **Variables** tab.

3. Add two variables:

   | Variable name             | Value |
   |---------------------------|--------|
   | `GOOGLE_SHEET_ID`         | The same spreadsheet ID you used in D (e.g. `1ABC...xyz123`). |
   | `GOOGLE_CREDENTIALS_JSON` | The **exact same** one-line JSON string from Part C. Paste the whole line. |

4. **Important for Railway:**
   - Paste the JSON as **one line**.
   - Do **not** add extra quotes around the value in the UI.
   - If Railway has a “multi-line” or “block” value option, you can paste the one-line JSON there to avoid trimming.

5. **Redeploy** the service (e.g. trigger a deploy or push a commit) so the new variables are picked up.

6. Test: open `https://your-app.railway.app/option_alpha_trigger` once, then check the same Google Sheet for another new row.

---

## Part F: Verify and troubleshoot

### Check that a row was added

- Columns should match: **Timestamp_ET**, **Signal**, **Should_Trade**, **Reason**, **Composite_Score**, **Category**, **IV_RV_Score**, **IV_RV_Ratio**, **VIX1D**, **Realized_Vol_10d**, **Trend_Score**, **Trend_5d_Chg_Pct**, **GPT_Score**, **GPT_Category**, **GPT_Key_Risk**, **Webhook_Success**, **SPX_Current**, **Raw_Articles**, **Sent_To_GPT**, **GPT_Reasoning**.

### Common issues

| Problem | What to check |
|--------|----------------|
| No row appears | Sheet shared with the **service account email** as **Editor**? GOOGLE_SHEET_ID and GOOGLE_CREDENTIALS_JSON set correctly? Check app logs for “Sheets append failed” or “Invalid GOOGLE_CREDENTIALS_JSON”. |
| “Invalid GOOGLE_CREDENTIALS_JSON” | JSON must be valid one-line. Use the Python one-liner in Part C and paste the full output. Ensure no truncation when pasting into .config or Railway. |
| “SpreadsheetNotFound” or permission error | Confirm the sheet is shared with the **client_email** from the JSON (Editor). |
| Local works, Railway doesn’t | Railway: redeploy after adding variables. Ensure the entire JSON is in GOOGLE_CREDENTIALS_JSON (no line breaks in the env value unless your platform supports them). |

### Optional: test without sending real webhooks

You can trigger the signal endpoint as usual; the webhook and Sheets logging are independent. If webhooks are configured, they’ll fire. To only test Sheets, you can temporarily point webhook URLs to a test endpoint or leave them as-is and just confirm the new row in the sheet.

---

## Quick reference

- **Spreadsheet ID:** from URL `.../d/<SPREADSHEET_ID>/edit`.
- **Service account email:** in the JSON key file, `"client_email"`.
- **One-line JSON:** use `python -c "import json; print(json.dumps(json.load(open(r'path/to/key.json')), separators=(',',':')))"` and paste the output.
- **Local:** `.config` → `[GOOGLE_SHEETS]` with `GOOGLE_SHEET_ID` and `GOOGLE_CREDENTIALS_JSON`.
- **Railway:** Variables → `GOOGLE_SHEET_ID` and `GOOGLE_CREDENTIALS_JSON` (same values), then redeploy.
