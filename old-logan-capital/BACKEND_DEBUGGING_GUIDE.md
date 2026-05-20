# Backend Debugging Guide

## Issues Reported by Frontend

### Issue 1: Discord showing actual names instead of "Team Member"
**What's happening:** Discord notifications are showing the actual person's name when they submit/review
**What should happen:** Show "Team Member" until all 3 reviews are complete

**Fix Required:**
In your Discord notification code, change this:
```javascript
// ❌ WRONG - Shows actual name
fields: [
    { name: 'Submitted by', value: submitterName, inline: true }
]

// ✅ CORRECT - Shows "Team Member"
fields: [
    { name: 'Submitted by', value: 'Team Member', inline: true }
]
```

**Where to fix:**
- `/api/submissions` POST endpoint → when sending Discord notification, use "Team Member"
- `/api/reviews` POST endpoint → when sending Discord notification, use "Team Member"
- ONLY show actual names once all 3 reviews are complete

---

### Issue 2: New scoring fields not being returned by API
**What's happening:** Frontend shows "0.00/10" and "undefined/10" for most scores
**What's working:** Only `confidence_level` shows correct average (8.75/10)

**Problem:** API responses are missing the new scoring fields

**Check Your Code:**

#### In GET `/api/submissions/:id`
Your response should include ALL these fields for submissions and reviews:

```javascript
// Submission object MUST include:
{
    id: 1,
    ticker: "AAPL",
    company_name: "Apple Inc.",
    submitter_name: "Paxton Thompson",

    // OLD FIELD (already working)
    confidence_level: 8,  // This works

    // NEW FIELDS (probably missing)
    technical_score: 7,
    fundamentals_score: 9,
    theme_score: 4,
    sector_score: 3,
    canslim_c: 7,
    canslim_a: 8,
    canslim_n: 6,
    canslim_s: 7,
    canslim_l: 8,
    canslim_i: 9,
    canslim_m: 7,
    final_score: 7.57,

    // Other fields
    reasoning: "...",
    price_target: "$200",
    time_horizon: "Long",

    // Reviews array - each review also needs all scoring fields
    reviews: [
        {
            reviewer_name: "Alex Evenson",
            confidence_level: 7,
            technical_score: 8,  // These are probably missing
            fundamentals_score: 8,
            theme_score: 3,
            sector_score: 4,
            canslim_c: 8,
            canslim_a: 7,
            canslim_n: 7,
            canslim_s: 8,
            canslim_l: 7,
            canslim_i: 8,
            canslim_m: 6,
            final_score: 7.43,
            reasoning: "...",
            // other review fields
        }
    ]
}
```

**Likely Problem:**
Your SQL SELECT statement is probably only querying the old `confidence_level` field and not the new fields.

**Check your database query:**
```sql
-- ❌ WRONG - Only selects old fields
SELECT id, ticker, company_name, submitter_name, confidence_level, reasoning
FROM submissions WHERE id = $1;

-- ✅ CORRECT - Selects all scoring fields
SELECT
    id, ticker, company_name, submitter_name, reasoning, price_target, time_horizon, sector,
    confidence_level, technical_score, fundamentals_score, theme_score, sector_score,
    canslim_c, canslim_a, canslim_n, canslim_s, canslim_l, canslim_i, canslim_m, final_score
FROM submissions WHERE id = $1;
```

Same issue for reviews table:
```sql
-- ✅ CORRECT - Select ALL scoring fields from reviews
SELECT
    id, submission_id, reviewer_name, reasoning, price_target, time_horizon,
    confidence_level, technical_score, fundamentals_score, theme_score, sector_score,
    canslim_c, canslim_a, canslim_n, canslim_s, canslim_l, canslim_i, canslim_m, final_score
FROM reviews WHERE submission_id = $1;
```

---

### Issue 3: Sector auto-population not working
**What's happening:** When user enters ticker, sector field doesn't auto-populate
**Expected:** Should fetch sector from Yahoo Finance API

**Fix Required:**
The endpoint `/api/ticker-info/:ticker` is probably not implemented yet.

**Implementation (Python + yfinance):**
```python
import yfinance as yf

@app.route('/api/ticker-info/<ticker>', methods=['GET'])
def get_ticker_info(ticker):
    try:
        stock = yf.Ticker(ticker.upper())
        info = stock.info

        return jsonify({
            'ticker': ticker.upper(),
            'sector': info.get('sector', 'N/A'),
            'company_name': info.get('longName', ''),
            'industry': info.get('industry', '')
        }), 200
    except Exception as e:
        print(f"Error fetching ticker info: {e}")
        return jsonify({'error': 'Ticker not found'}), 404
```

**Implementation (Node.js + yahoo-finance2):**
```javascript
const yahooFinance = require('yahoo-finance2').default;

app.get('/api/ticker-info/:ticker', async (req, res) => {
    try {
        const { ticker } = req.params;
        const quote = await yahooFinance.quote(ticker.toUpperCase());

        res.json({
            ticker: ticker.toUpperCase(),
            sector: quote.sector || 'N/A',
            company_name: quote.longName || '',
            industry: quote.industry || ''
        });
    } catch (error) {
        console.error('Error fetching ticker info:', error);
        res.status(404).json({ error: 'Ticker not found' });
    }
});
```

**Install required package:**
- Python: `pip install yfinance`
- Node.js: `npm install yahoo-finance2`

---

## Testing Your Fixes

### 1. Test API Response
Make a request to your API and check the response:

```bash
# Get a submission with reviews
curl https://your-api.com/api/submissions/1
```

**What to look for:**
- Does the submission object have `technical_score`, `fundamentals_score`, etc?
- Do the review objects have all the scoring fields?
- Are the values numbers, not null?

### 2. Test Ticker Info Endpoint
```bash
# Test ticker lookup
curl https://your-api.com/api/ticker-info/AAPL
```

**Expected response:**
```json
{
    "ticker": "AAPL",
    "sector": "Technology",
    "company_name": "Apple Inc.",
    "industry": "Consumer Electronics"
}
```

### 3. Test Discord Notifications

**When submitting a ticker:**
Discord message should say:
```
🎯 New Ticker Submitted

Ticker: AAPL - Apple Inc.
Submitted by: Team Member  ← MUST say "Team Member"
Score: 7.57/10
...
```

**When reviewing a ticker:**
Discord message should say:
```
✅ Review Submitted

Ticker: AAPL - Apple Inc.
Reviewed by: Team Member  ← MUST say "Team Member"
Reviews Complete: 2/3
Score: 7.43/10
```

**After all 3 reviews complete:**
NOW show actual names:
```
🎉 All Reviews Complete!

Ticker: AAPL - Apple Inc.
Team Final Score: 7.50/10

Individual Scores:
• Paxton Thompson (Submitter): 7.57/10  ← NOW show names
• Alex Evenson: 7.43/10
• Garett Lake: 7.52/10
• Sam Thoresen: 7.48/10
```

---

## Quick Checklist

- [ ] Database has all 13 new scoring columns (technical_score, fundamentals_score, etc.)
- [ ] POST /api/submissions accepts and STORES all new scoring fields
- [ ] POST /api/reviews accepts and STORES all new scoring fields
- [ ] GET /api/submissions/:id RETURNS all scoring fields for submission
- [ ] GET /api/submissions/:id RETURNS all scoring fields for each review
- [ ] GET /api/submissions calculates and returns `avg_final_score`
- [ ] Discord submission notification shows "Team Member" not actual name
- [ ] Discord review notification shows "Team Member" not actual name
- [ ] Discord "all complete" notification shows all actual names
- [ ] GET /api/ticker-info/:ticker endpoint exists and works
- [ ] yfinance or yahoo-finance2 package installed

---

## How Frontend Will Debug

The frontend now has console logging. When viewing a submission, check browser console (F12) for:

```
=== BACKEND RESPONSE DEBUG ===
Full submission object: { ... }
Submission scores: {
    confidence: 8,
    technical: undefined,  ← If undefined, backend isn't returning this field
    fundamentals: undefined,
    ...
}
============================
```

If you see `undefined` values, those fields are missing from your API response.

---

## Questions?

If you're stuck:
1. Check your database - do the columns exist? `SELECT * FROM submissions LIMIT 1;`
2. Check your SELECT query - are you selecting all the new columns?
3. Check your INSERT query - are you inserting all the new values?
4. Check the actual API response in your logs or with curl

The frontend is ready and working - it just needs the backend to return the data in the expected format.
