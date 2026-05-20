# Backend Implementation Requirements for Watchlist Scoring System

This document outlines all the backend changes required to support the new scoring system and features for the Logan Capital Watchlist application.

## Overview
The watchlist now uses a comprehensive scoring system with 6 equally-weighted categories that produce a final score out of 10. Both the initial submission and each of the 3 reviews need to capture these scores.

---

## 1. Database Schema Updates

### 1.1 Submissions Table
Add the following columns to the `submissions` table:

```sql
-- Core Scoring Fields (1-10)
ALTER TABLE submissions ADD COLUMN confidence_level INT CHECK (confidence_level BETWEEN 1 AND 10);
ALTER TABLE submissions ADD COLUMN technical_score INT CHECK (technical_score BETWEEN 1 AND 10);
ALTER TABLE submissions ADD COLUMN fundamentals_score INT CHECK (fundamentals_score BETWEEN 1 AND 10);

-- Theme and Sector Scores (1-5, but weighted ×2 to equal 1-10)
ALTER TABLE submissions ADD COLUMN theme_score INT CHECK (theme_score BETWEEN 1 AND 5);
ALTER TABLE submissions ADD COLUMN sector_score INT CHECK (sector_score BETWEEN 1 AND 5);

-- CANSLIM Scores (7 fields, each 1-10)
ALTER TABLE submissions ADD COLUMN canslim_c INT CHECK (canslim_c BETWEEN 1 AND 10);  -- Current Earnings
ALTER TABLE submissions ADD COLUMN canslim_a INT CHECK (canslim_a BETWEEN 1 AND 10);  -- Annual Earnings
ALTER TABLE submissions ADD COLUMN canslim_n INT CHECK (canslim_n BETWEEN 1 AND 10);  -- New Product/Service
ALTER TABLE submissions ADD COLUMN canslim_s INT CHECK (canslim_s BETWEEN 1 AND 10);  -- Supply & Demand
ALTER TABLE submissions ADD COLUMN canslim_l INT CHECK (canslim_l BETWEEN 1 AND 10);  -- Leader/Laggard
ALTER TABLE submissions ADD COLUMN canslim_i INT CHECK (canslim_i BETWEEN 1 AND 10);  -- Institutional
ALTER TABLE submissions ADD COLUMN canslim_m INT CHECK (canslim_m BETWEEN 1 AND 10);  -- Market Direction

-- Final Score (calculated, stored as DECIMAL for precision)
ALTER TABLE submissions ADD COLUMN final_score DECIMAL(4,2);
```

### 1.2 Reviews Table
Add the same scoring columns to the `reviews` table:

```sql
-- Core Scoring Fields
ALTER TABLE reviews ADD COLUMN confidence_level INT CHECK (confidence_level BETWEEN 1 AND 10);
ALTER TABLE reviews ADD COLUMN technical_score INT CHECK (technical_score BETWEEN 1 AND 10);
ALTER TABLE reviews ADD COLUMN fundamentals_score INT CHECK (fundamentals_score BETWEEN 1 AND 10);
ALTER TABLE reviews ADD COLUMN theme_score INT CHECK (theme_score BETWEEN 1 AND 5);
ALTER TABLE reviews ADD COLUMN sector_score INT CHECK (sector_score BETWEEN 1 AND 5);

-- CANSLIM Scores
ALTER TABLE reviews ADD COLUMN canslim_c INT CHECK (canslim_c BETWEEN 1 AND 10);
ALTER TABLE reviews ADD COLUMN canslim_a INT CHECK (canslim_a BETWEEN 1 AND 10);
ALTER TABLE reviews ADD COLUMN canslim_n INT CHECK (canslim_n BETWEEN 1 AND 10);
ALTER TABLE reviews ADD COLUMN canslim_s INT CHECK (canslim_s BETWEEN 1 AND 10);
ALTER TABLE reviews ADD COLUMN canslim_l INT CHECK (canslim_l BETWEEN 1 AND 10);
ALTER TABLE reviews ADD COLUMN canslim_i INT CHECK (canslim_i BETWEEN 1 AND 10);
ALTER TABLE reviews ADD COLUMN canslim_m INT CHECK (canslim_m BETWEEN 1 AND 10);

-- Final Score
ALTER TABLE reviews ADD COLUMN final_score DECIMAL(4,2);
```

### 1.3 Watchlist Table
Add columns to track review cycles:

```sql
ALTER TABLE watchlist ADD COLUMN next_review_date DATE;
ALTER TABLE watchlist ADD COLUMN last_review_date DATE;
ALTER TABLE watchlist ADD COLUMN avg_final_score DECIMAL(4,2);
```

### 1.4 New Archive Table (Optional but Recommended)
Create a new table to store archived tickers:

```sql
CREATE TABLE archived_watchlist (
    id SERIAL PRIMARY KEY,
    submission_id INT REFERENCES submissions(id),
    ticker VARCHAR(10),
    company_name VARCHAR(255),
    archived_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    archived_by VARCHAR(100),
    archive_reason TEXT,
    final_avg_score DECIMAL(4,2),
    time_on_watchlist_days INT
);
```

---

## 2. Final Score Calculation

### Formula
The final score is calculated using this formula:

```
Final Score = (
    Confidence +
    Technical +
    Fundamentals +
    (Theme × 2) +
    (Sector × 2) +
    CANSLIM_Average
) / 6

Where CANSLIM_Average = (C + A + N + S + L + I + M) / 7
```

### Example Calculation
```javascript
// Example scores
confidence = 8
technical = 7
fundamentals = 9
theme = 4 (weighted: 4 × 2 = 8)
sector = 3 (weighted: 3 × 2 = 6)
canslim = [7, 8, 6, 7, 8, 9, 7] → average = 7.43

final_score = (8 + 7 + 9 + 8 + 6 + 7.43) / 6 = 7.57
```

### Implementation
- Frontend calculates and sends `finalScore` with submissions/reviews
- Backend should validate and recalculate to ensure accuracy
- Store as DECIMAL(4,2) for 2 decimal places (e.g., 7.57)

---

## 3. API Endpoint Updates

### 3.1 POST `/api/submissions`
**Updated Request Body:**
```json
{
  "ticker": "AAPL",
  "submitterName": "Paxton Thompson",
  "companyName": "Apple Inc.",
  "reasoning": "Strong fundamentals...",
  "priceTarget": "$200",
  "timeHorizon": "Long",
  "sector": "Technology",

  // New Scoring Fields
  "confidenceLevel": 8,
  "technicalScore": 7,
  "fundamentalsScore": 9,
  "themeScore": 4,
  "sectorScore": 3,
  "canslim_c": 7,
  "canslim_a": 8,
  "canslim_n": 6,
  "canslim_s": 7,
  "canslim_l": 8,
  "canslim_i": 9,
  "canslim_m": 7,
  "finalScore": 7.57,

  "attachments": [files]
}
```

### 3.2 POST `/api/reviews`
**Updated Request Body:** (same scoring fields as submissions)
```json
{
  "submissionId": 123,
  "reviewerName": "Alex Evenson",
  "reasoning": "Analysis...",
  "priceTarget": "$190",
  "timeHorizon": "Medium",

  // Scoring fields (same as submission)
  "confidenceLevel": 7,
  "technicalScore": 8,
  // ... all other scoring fields
  "finalScore": 7.43,

  "attachments": [files]
}
```

### 3.3 GET `/api/submissions`
**Updated Response:**
Add `avg_final_score` to the response for each submission:

```json
[
  {
    "id": 1,
    "ticker": "AAPL",
    "company_name": "Apple Inc.",
    "submitter_name": "Paxton Thompson",
    "review_count": 3,
    "avg_final_score": 7.50,  // NEW: Average of all 4 final scores
    "status": "under_review",
    "created_at": "2024-01-15"
  }
]
```

### 3.4 GET `/api/submissions/:id`
**Updated Response:**
Include ALL scoring fields for submission and reviews:

```json
{
  "id": 1,
  "ticker": "AAPL",
  "company_name": "Apple Inc.",
  "submitter_name": "Paxton Thompson",

  // Original fields
  "reasoning": "...",
  "price_target": "$200",
  "time_horizon": "Long",
  "sector": "Technology",

  // NEW: All scoring fields from submission
  "confidence_level": 8,
  "technical_score": 7,
  "fundamentals_score": 9,
  "theme_score": 4,
  "sector_score": 3,
  "canslim_c": 7,
  "canslim_a": 8,
  "canslim_n": 6,
  "canslim_s": 7,
  "canslim_l": 8,
  "canslim_i": 9,
  "canslim_m": 7,
  "final_score": 7.57,

  // Reviews with all scoring fields
  "reviews": [
    {
      "id": 1,
      "reviewer_name": "Alex Evenson",
      "reasoning": "...",
      "confidence_level": 7,
      "technical_score": 8,
      // ... all scoring fields
      "final_score": 7.43
    }
  ],

  "reviewsComplete": true,
  "attachments": []
}
```

### 3.5 GET `/api/watchlist`
**Updated Response:**
```json
[
  {
    "submission_id": 1,
    "ticker": "AAPL",
    "company_name": "Apple Inc.",
    "submitter_name": "Paxton Thompson",
    "avg_final_score": 7.50,  // NEW: Average of 4 scores
    "sector": "Technology",
    "time_horizon": "Long",
    "added_at": "2024-01-20",
    "next_review_date": "2024-02-19"  // NEW: 30 days from added_at
  }
]
```

### 3.6 NEW: GET `/api/ticker-info/:ticker`
**Purpose:** Auto-populate sector when ticker is entered

**Implementation using yfinance (Python example):**
```python
import yfinance as yf

@app.route('/api/ticker-info/<ticker>', methods=['GET'])
def get_ticker_info(ticker):
    try:
        stock = yf.Ticker(ticker)
        info = stock.info

        return jsonify({
            'ticker': ticker.upper(),
            'sector': info.get('sector', 'N/A'),
            'company_name': info.get('longName', ''),
            'industry': info.get('industry', '')
        }), 200
    except Exception as e:
        return jsonify({'error': 'Ticker not found'}), 404
```

**Response Example:**
```json
{
  "ticker": "AAPL",
  "sector": "Technology",
  "company_name": "Apple Inc.",
  "industry": "Consumer Electronics"
}
```

---

## 4. Discord Webhook Integration

### 4.1 Webhook Configuration
- Store Discord webhook URL in environment variable: `DISCORD_WEBHOOK_URL`
- Webhook should point to a specific channel for watchlist notifications

### 4.2 Notification Events

#### Event 1: New Ticker Submitted
**Trigger:** When POST `/api/submissions` is successful

**Discord Message Format:**
```
🎯 **New Ticker Submitted**

**Ticker:** AAPL - Apple Inc.
**Submitted by:** Team Member  ← IMPORTANT: Don't show actual name
**Sector:** Technology
**Time Horizon:** Long Term

**Score:** 7.57/10

📊 Team: Please review this submission independently.
```

**Implementation Notes:**
- DO NOT include the submitter's actual name
- Use "Team Member" to prevent bias
- Only show the submitter's score (their final_score), not the breakdown

#### Event 2: Review Submitted
**Trigger:** When POST `/api/reviews` is successful

**Discord Message Format:**
```
✅ **Review Submitted**

**Ticker:** AAPL - Apple Inc.
**Reviewed by:** Team Member  ← IMPORTANT: Don't show actual name
**Reviews Complete:** 2/3

**Score:** 7.43/10

📊 Waiting for 1 more review before revealing all results.
```

#### Event 3: All Reviews Complete
**Trigger:** When the 3rd review is submitted

**Discord Message Format:**
```
🎉 **All Reviews Complete!**

**Ticker:** AAPL - Apple Inc.

**Team Final Score:** 7.50/10

**Individual Scores:**
• Paxton Thompson (Submitter): 7.57/10
• Alex Evenson: 7.43/10
• Garett Lake: 7.52/10
• Sam Thoresen: 7.48/10

✅ Ready for final approval to add to watchlist.
```

**Implementation Notes:**
- NOW show all names since reviews are complete
- Show individual final scores
- Calculate and show team average

#### Event 4: Ticker Approved for Watchlist
**Trigger:** When POST `/api/watchlist/approve/:id` is successful

**Discord Message Format:**
```
🚀 **Added to Watchlist**

**Ticker:** AAPL - Apple Inc.
**Final Team Score:** 7.50/10
**Next Review Date:** 2024-02-19 (30 days)

🔔 You'll be notified in 30 days to review this ticker.
```

#### Event 5: Monthly Review Reminder (NEW)
**Trigger:** 30 days after ticker added to watchlist

**Discord Message Format:**
```
⏰ **Monthly Watchlist Review**

**Ticker:** AAPL - Apple Inc.
**Added:** 2024-01-20
**Current Score:** 7.50/10
**Days on Watchlist:** 30

❓ **Action Required:**
Team vote: Keep or Archive this ticker?

React with:
✅ = Keep on watchlist
❌ = Archive (remove from active watchlist)
```

**Implementation:**
- Use a background job/cron to check `next_review_date` daily
- Send notification when `next_review_date` = today
- After notification, update `next_review_date` to 30 days later
- Track votes (optional: can be manual or implement vote tracking)

### 4.3 Discord Webhook Code Example (Node.js/Express)

```javascript
const axios = require('axios');

async function sendDiscordNotification(type, data) {
    const webhookUrl = process.env.DISCORD_WEBHOOK_URL;

    let embed = {};

    switch(type) {
        case 'submission':
            embed = {
                title: '🎯 New Ticker Submitted',
                color: 0xC9A962, // Gold color
                fields: [
                    { name: 'Ticker', value: `${data.ticker} - ${data.company_name}`, inline: false },
                    { name: 'Submitted by', value: 'Team Member', inline: true },
                    { name: 'Sector', value: data.sector, inline: true },
                    { name: 'Score', value: `${data.final_score}/10`, inline: true },
                    { name: 'Time Horizon', value: data.time_horizon, inline: true }
                ],
                footer: { text: '📊 Please review this submission independently.' }
            };
            break;

        case 'review':
            embed = {
                title: '✅ Review Submitted',
                color: 0x2D5A4A, // Forest green
                fields: [
                    { name: 'Ticker', value: `${data.ticker} - ${data.company_name}`, inline: false },
                    { name: 'Reviewed by', value: 'Team Member', inline: true },
                    { name: 'Reviews Complete', value: `${data.review_count}/3`, inline: true },
                    { name: 'Score', value: `${data.final_score}/10`, inline: true }
                ],
                footer: { text: `📊 Waiting for ${3 - data.review_count} more review(s).` }
            };
            break;

        case 'reviews_complete':
            const individualScores = data.all_scores.map(s =>
                `• ${s.name}${s.is_submitter ? ' (Submitter)' : ''}: ${s.final_score}/10`
            ).join('\n');

            embed = {
                title: '🎉 All Reviews Complete!',
                color: 0x1A3A2E, // Dark forest
                fields: [
                    { name: 'Ticker', value: `${data.ticker} - ${data.company_name}`, inline: false },
                    { name: 'Team Final Score', value: `${data.avg_final_score}/10`, inline: false },
                    { name: 'Individual Scores', value: individualScores, inline: false }
                ],
                footer: { text: '✅ Ready for final approval to add to watchlist.' }
            };
            break;

        case 'approved':
            embed = {
                title: '🚀 Added to Watchlist',
                color: 0xC9A962,
                fields: [
                    { name: 'Ticker', value: `${data.ticker} - ${data.company_name}`, inline: false },
                    { name: 'Final Team Score', value: `${data.avg_final_score}/10`, inline: true },
                    { name: 'Next Review Date', value: data.next_review_date, inline: true }
                ],
                footer: { text: '🔔 You\'ll be notified in 30 days to review this ticker.' }
            };
            break;

        case 'monthly_review':
            embed = {
                title: '⏰ Monthly Watchlist Review',
                color: 0xC9A962,
                fields: [
                    { name: 'Ticker', value: `${data.ticker} - ${data.company_name}`, inline: false },
                    { name: 'Added', value: data.added_at, inline: true },
                    { name: 'Current Score', value: `${data.avg_final_score}/10`, inline: true },
                    { name: 'Days on Watchlist', value: `${data.days_on_watchlist}`, inline: true },
                    { name: 'Action Required', value: '**Team vote: Keep or Archive this ticker?**\n\nReact with:\n✅ = Keep on watchlist\n❌ = Archive', inline: false }
                ]
            };
            break;
    }

    try {
        await axios.post(webhookUrl, {
            embeds: [embed]
        });
    } catch (error) {
        console.error('Discord notification failed:', error);
    }
}

// Usage in your API endpoints:
// await sendDiscordNotification('submission', submissionData);
// await sendDiscordNotification('review', reviewData);
// await sendDiscordNotification('reviews_complete', completeData);
// await sendDiscordNotification('approved', approvalData);
```

---

## 5. Archive System

### 5.1 Archive Endpoint
**NEW: POST `/api/watchlist/archive/:submission_id`**

**Request Body:**
```json
{
  "archived_by": "Paxton Thompson",
  "archive_reason": "Price target reached" // or "No longer fits thesis"
}
```

**Implementation:**
```javascript
router.post('/watchlist/archive/:submission_id', async (req, res) => {
    const { submission_id } = req.params;
    const { archived_by, archive_reason } = req.body;

    try {
        // Get current watchlist item
        const watchlistItem = await db.query(
            'SELECT * FROM watchlist WHERE submission_id = $1',
            [submission_id]
        );

        if (!watchlistItem.rows[0]) {
            return res.status(404).json({ error: 'Watchlist item not found' });
        }

        const item = watchlistItem.rows[0];
        const daysOnWatchlist = Math.floor(
            (new Date() - new Date(item.added_at)) / (1000 * 60 * 60 * 24)
        );

        // Insert into archive
        await db.query(`
            INSERT INTO archived_watchlist
            (submission_id, ticker, company_name, archived_by, archive_reason,
             final_avg_score, time_on_watchlist_days)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
        `, [
            submission_id,
            item.ticker,
            item.company_name,
            archived_by,
            archive_reason,
            item.avg_final_score,
            daysOnWatchlist
        ]);

        // Remove from active watchlist
        await db.query('DELETE FROM watchlist WHERE submission_id = $1', [submission_id]);

        // Optional: Send Discord notification
        await sendDiscordNotification('archived', {
            ticker: item.ticker,
            company_name: item.company_name,
            archived_by: archived_by,
            reason: archive_reason,
            days_on_watchlist: daysOnWatchlist
        });

        res.json({ message: 'Ticker archived successfully' });
    } catch (error) {
        console.error('Archive error:', error);
        res.status(500).json({ error: 'Failed to archive ticker' });
    }
});
```

### 5.2 Get Archived Items
**NEW: GET `/api/watchlist/archived`**

Returns list of all archived tickers with metadata.

---

## 6. Monthly Review Cron Job

### 6.1 Implementation (Node.js with node-cron)

```javascript
const cron = require('node-cron');

// Run every day at 9:00 AM
cron.schedule('0 9 * * *', async () => {
    console.log('Running daily watchlist review check...');

    try {
        // Find tickers due for review today
        const dueForReview = await db.query(`
            SELECT w.*, s.ticker, s.company_name
            FROM watchlist w
            JOIN submissions s ON w.submission_id = s.id
            WHERE w.next_review_date <= CURRENT_DATE
        `);

        for (const item of dueForReview.rows) {
            // Send Discord notification
            await sendDiscordNotification('monthly_review', {
                ticker: item.ticker,
                company_name: item.company_name,
                added_at: item.added_at,
                avg_final_score: item.avg_final_score,
                days_on_watchlist: Math.floor(
                    (new Date() - new Date(item.added_at)) / (1000 * 60 * 60 * 24)
                )
            });

            // Update next review date to 30 days from now
            await db.query(`
                UPDATE watchlist
                SET next_review_date = CURRENT_DATE + INTERVAL '30 days',
                    last_review_date = CURRENT_DATE
                WHERE submission_id = $1
            `, [item.submission_id]);
        }

        console.log(`Sent ${dueForReview.rows.length} review notifications`);
    } catch (error) {
        console.error('Cron job error:', error);
    }
});
```

---

## 7. Testing Checklist

### Database
- [ ] All new columns added to submissions table
- [ ] All new columns added to reviews table
- [ ] Watchlist table updated with review dates
- [ ] Archive table created
- [ ] All constraints and checks working

### API Endpoints
- [ ] POST /api/submissions accepts and stores all new scoring fields
- [ ] POST /api/reviews accepts and stores all new scoring fields
- [ ] GET /api/submissions returns avg_final_score
- [ ] GET /api/submissions/:id returns all scoring fields for submission and reviews
- [ ] GET /api/watchlist returns avg_final_score and next_review_date
- [ ] GET /api/ticker-info/:ticker works with Yahoo Finance
- [ ] POST /api/watchlist/archive/:id successfully archives tickers
- [ ] GET /api/watchlist/archived returns archived items

### Discord Notifications
- [ ] Webhook URL configured
- [ ] Submission notification shows "Team Member" (not actual name)
- [ ] Review notification shows "Team Member" (not actual name)
- [ ] All reviews complete notification shows all names and scores
- [ ] Approved notification includes next review date
- [ ] Monthly review notification triggers correctly
- [ ] All embeds display correctly in Discord

### Score Calculations
- [ ] Final score calculated correctly using formula
- [ ] Team average calculated correctly (4 people: 1 submitter + 3 reviewers)
- [ ] Scores display with 2 decimal precision
- [ ] Validation ensures scores are within correct ranges

### Monthly Reviews
- [ ] Cron job configured to run daily
- [ ] next_review_date set to 30 days after approval
- [ ] Notifications trigger when next_review_date is today
- [ ] next_review_date updates after notification sent

---

## 8. Environment Variables

Add these to your `.env` file:

```env
DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/YOUR_WEBHOOK_URL
CRON_SCHEDULE=0 9 * * *  # 9:00 AM daily
```

---

## Summary of Key Changes

1. **13 new scoring columns** added to both submissions and reviews tables
2. **Final score calculation** using weighted average formula
3. **New ticker-info endpoint** for Yahoo Finance integration
4. **Discord webhook** integration with 5 notification types
5. **Name anonymity** until all 3 reviews complete
6. **30-day review cycle** with automated notifications
7. **Archive system** for removed tickers
8. **Daily cron job** for review reminders

---

## Questions or Issues?

Contact the frontend developer (Paxton) if you need clarification on:
- Frontend expectations for API response format
- Discord message formatting preferences
- Score calculation edge cases
- Any other integration concerns
