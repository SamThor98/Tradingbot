const axios = require('axios');

const WEBHOOK_URL = process.env.DISCORD_WEBHOOK_URL || 'https://discordapp.com/api/webhooks/1500951215226355872/QYoU0Xk0f4CvDATn1SAYl2qxaZK7wVx39SaDTwOuFqgywcfj5cszM00FTO76pNuQcfxP';

// Helper function: Send webhook with retry logic for rate limiting
async function sendWebhookWithRetry(payload, maxRetries = 3) {
    for (let attempt = 1; attempt <= maxRetries; attempt++) {
        try {
            await axios.post(WEBHOOK_URL, payload, {
                headers: { 'Content-Type': 'application/json' },
                timeout: 10000
            });

            // Success - log only on retry attempts to reduce noise
            if (attempt > 1) {
                console.log(`[Discord] ✓ Webhook sent successfully after ${attempt} attempts`);
            }
            return true;
        } catch (error) {
            const status = error.response?.status;
            const retryAfter = error.response?.headers?.['retry-after'] || error.response?.data?.retry_after;

            // Handle rate limiting (429) with exponential backoff
            if (status === 429 && attempt < maxRetries) {
                // Use Discord's retry-after if provided, otherwise exponential backoff
                // Cap at 30 seconds to prevent excessive delays
                const baseWaitTime = retryAfter ? parseInt(retryAfter) * 1000 : Math.pow(2, attempt) * 1000;
                const waitTime = Math.min(baseWaitTime, 30000);
                console.warn(`[Discord] Rate limited (429). Retrying in ${waitTime}ms (attempt ${attempt}/${maxRetries})...`);
                await new Promise(resolve => setTimeout(resolve, waitTime));
                continue;
            }

            // Log error on final attempt
            if (attempt === maxRetries) {
                console.error(`[Discord] ✗ Webhook failed after ${maxRetries} attempts:`, {
                    status: status,
                    message: error.message,
                    data: error.response?.data
                });
            }
        }
    }
    return false;
}

async function sendNewSubmissionNotification(ticker, submitterName) {
    const payload = {
        embeds: [{
            title: '📊 New Watchlist Submission',
            description: `**Team Member** has submitted **${ticker}** for review`,
            color: 0x1A3A2E, // Forest green
            timestamp: new Date().toISOString(),
            footer: {
                text: 'Old Logan Capital Watchlist System'
            }
        }]
    };
    await sendWebhookWithRetry(payload);
}

async function sendReviewCompleteNotification(ticker, reviewerName, reviewsCompleted, totalReviews) {
    const payload = {
        embeds: [{
            title: '✅ Review Completed',
            description: `**Team Member** has completed their review of **${ticker}**\n\nProgress: ${reviewsCompleted}/${totalReviews} reviews complete`,
            color: 0x2D5A4A, // Forest light
            timestamp: new Date().toISOString(),
            footer: {
                text: 'Old Logan Capital Watchlist System'
            }
        }]
    };
    await sendWebhookWithRetry(payload);
}

async function sendAllReviewsCompleteNotification(ticker, avgConfidence) {
    const confidenceStars = '⭐'.repeat(Math.round(avgConfidence));

    const payload = {
        embeds: [{
            title: '🎯 Ready for Review',
            description: `All reviews for **${ticker}** are complete!\n\n**Average Confidence:** ${avgConfidence.toFixed(2)}/5 ${confidenceStars}\n\nResults are now visible to the team.`,
            color: 0xC9A962, // Gold
            timestamp: new Date().toISOString(),
            footer: {
                text: 'Old Logan Capital Watchlist System'
            }
        }]
    };
    await sendWebhookWithRetry(payload);
}

module.exports = {
    sendNewSubmissionNotification,
    sendReviewCompleteNotification,
    sendAllReviewsCompleteNotification
};
