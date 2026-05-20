const express = require('express');
const session = require('express-session');
const cors = require('cors');
const bcrypt = require('bcryptjs');
const multer = require('multer');
const path = require('path');
const fs = require('fs');

const db = require('./database');
const discord = require('./discord');

const app = express();
const PORT = process.env.PORT || 3000;

// Use persistent disk path on Render, local path otherwise
const dataDir = process.env.RENDER ? '/opt/render/project/src/data' : __dirname;
const uploadsDir = path.join(dataDir, 'uploads');

// Ensure uploads directory exists
if (!fs.existsSync(uploadsDir)) {
    fs.mkdirSync(uploadsDir, { recursive: true });
}

// Middleware - Allow both production and local origins
const allowedOrigins = [
    'https://oldlogancapital.com',
    'http://localhost:8081',
    'http://127.0.0.1:8081'
];

app.use(cors({
    origin: function(origin, callback) {
        // Allow requests with no origin (like mobile apps or curl requests)
        if (!origin) return callback(null, true);

        // Allow any localhost/127.0.0.1 origin for development
        if (origin && (origin.startsWith('http://localhost:') || origin.startsWith('http://127.0.0.1:'))) {
            return callback(null, true);
        }

        if (allowedOrigins.indexOf(origin) !== -1) {
            callback(null, true);
        } else {
            callback(new Error('Not allowed by CORS'));
        }
    },
    credentials: true
}));
app.use(express.json());
app.use(express.static(path.join(__dirname, '..')));

app.use(session({
    secret: 'OLC-watchlist-secret-key-2025',
    resave: false,
    saveUninitialized: false,
    cookie: {
        httpOnly: true,
        secure: process.env.NODE_ENV === 'production', // Use secure cookies in production (HTTPS), allow HTTP in development
        sameSite: 'lax', // 'lax' works for same-site subdomains (api.oldlogancapital.com and oldlogancapital.com)
        domain: process.env.NODE_ENV === 'production' ? '.oldlogancapital.com' : undefined, // Share cookie across subdomains in production
        maxAge: 24 * 60 * 60 * 1000 // 24 hours
    }
}));

// File upload configuration
const storage = multer.diskStorage({
    destination: (req, file, cb) => {
        cb(null, uploadsDir);
    },
    filename: (req, file, cb) => {
        const uniqueName = `${Date.now()}-${Math.round(Math.random() * 1E9)}${path.extname(file.originalname)}`;
        cb(null, uniqueName);
    }
});

const upload = multer({
    storage: storage,
    limits: { fileSize: 25 * 1024 * 1024 }, // 25MB
    fileFilter: (req, file, cb) => {
        const allowedTypes = ['.pdf', '.png', '.jpg', '.jpeg', '.xlsx', '.xls', '.doc', '.docx', '.csv'];
        const ext = path.extname(file.originalname).toLowerCase();
        if (allowedTypes.includes(ext)) {
            cb(null, true);
        } else {
            cb(new Error('Invalid file type'));
        }
    }
});

// Authentication middleware
function requireAuth(req, res, next) {
    if (req.session.userId) {
        next();
    } else {
        res.status(401).json({ error: 'Not authenticated' });
    }
}

// ===== AUTHENTICATION ROUTES =====

app.post('/api/login', async (req, res) => {
    try {
        const { username, password } = req.body;

        const users = db.query('SELECT * FROM users WHERE username = ?', [username]);

        if (users.length === 0) {
            return res.status(401).json({ error: 'Invalid credentials' });
        }

        const user = users[0];
        const isValid = await bcrypt.compare(password, user.password);

        if (!isValid) {
            return res.status(401).json({ error: 'Invalid credentials' });
        }

        req.session.userId = user.id;
        req.session.username = user.username;
        req.session.fullName = user.full_name;

        res.json({
            id: user.id,
            username: user.username,
            fullName: user.full_name
        });
    } catch (error) {
        res.status(500).json({ error: error.message });
    }
});

app.post('/api/logout', (req, res) => {
    req.session.destroy();
    res.json({ success: true });
});

app.get('/api/me', (req, res) => {
    res.json({
        id: req.session.userId,
        username: req.session.username,
        fullName: req.session.fullName
    });
});

// ===== SUBMISSION ROUTES =====

app.post('/api/submissions', upload.array('attachments', 5), async (req, res) => {
    try {
        const {
            ticker,
            companyName,
            confidenceLevel,
            technicalScore,
            fundamentalsScore,
            themeScore,
            sectorScore,
            finalScore,
            reasoning,
            entryRange,
            sellRange,
            timeHorizon,
            sector,
            submitterName
        } = req.body;

        // Get submitter user ID from the database
        const submitter = db.query('SELECT id FROM users WHERE full_name = ?', [submitterName]);
        const submitterId = submitter.length > 0 ? submitter[0].id : 1;

        // Insert submission with all scoring fields
        db.run(`
            INSERT INTO submissions (
                ticker, company_name, submitter_id, submitter_name,
                confidence_level, technical_score, fundamentals_score, theme_score, sector_score,
                final_score, reasoning, entry_range, sell_range, time_horizon, sector, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'submitted')
        `, [
            ticker.toUpperCase(),
            companyName,
            submitterId,
            submitterName,
            confidenceLevel,
            technicalScore,
            fundamentalsScore,
            themeScore,
            sectorScore,
            finalScore,
            reasoning,
            entryRange || null,
            sellRange || null,
            timeHorizon,
            sector || null
        ]);

        const submissionId = db.getLastInsertId();

        // Handle file attachments
        if (req.files && req.files.length > 0) {
            req.files.forEach(file => {
                db.run(`
                    INSERT INTO attachments (submission_id, filename, filepath, file_type, file_size)
                    VALUES (?, ?, ?, ?, ?)
                `, [
                    submissionId,
                    file.originalname,
                    file.filename,
                    file.mimetype,
                    file.size
                ]);
            });
        }

        // Send Discord notification
        await discord.sendNewSubmissionNotification(ticker.toUpperCase(), submitterName);

        res.json({ success: true, submissionId });
    } catch (error) {
        res.status(500).json({ error: error.message });
    }
});

app.get('/api/submissions', (req, res) => {
    try {
        const submissions = db.query(`
            SELECT
                s.*,
                (SELECT COUNT(*) FROM reviews WHERE submission_id = s.id) as review_count,
                (SELECT AVG(confidence_level) FROM reviews WHERE submission_id = s.id) as avg_confidence,
                (SELECT AVG(final_score) FROM reviews WHERE submission_id = s.id) as avg_final_score
            FROM submissions s
            ORDER BY s.created_at DESC
        `);

        res.json(submissions);
    } catch (error) {
        res.status(500).json({ error: error.message });
    }
});

app.get('/api/submissions/:id', (req, res) => {
    try {
        const submissionId = req.params.id;

        const submission = db.query('SELECT * FROM submissions WHERE id = ?', [submissionId]);

        if (submission.length === 0) {
            return res.status(404).json({ error: 'Submission not found' });
        }

        const attachments = db.query('SELECT * FROM attachments WHERE submission_id = ?', [submissionId]);
        const reviews = db.query('SELECT * FROM reviews WHERE submission_id = ?', [submissionId]);

        // Check if all reviews are complete (3 reviews needed)
        const reviewsComplete = reviews.length >= 3;

        res.json({
            ...submission[0],
            attachments,
            reviews: reviewsComplete ? reviews : [],
            reviewsComplete
        });
    } catch (error) {
        res.status(500).json({ error: error.message });
    }
});

app.delete('/api/submissions/:id', (req, res) => {
    try {
        const submissionId = req.params.id;

        // Check if submission exists
        const submission = db.query('SELECT * FROM submissions WHERE id = ?', [submissionId]);
        if (submission.length === 0) {
            return res.status(404).json({ error: 'Submission not found' });
        }

        // Get all attachments for this submission to delete files
        const submissionAttachments = db.query('SELECT * FROM attachments WHERE submission_id = ?', [submissionId]);

        // Get all reviews and their attachments
        const reviews = db.query('SELECT id FROM reviews WHERE submission_id = ?', [submissionId]);
        const reviewIds = reviews.map(r => r.id);

        let reviewAttachments = [];
        if (reviewIds.length > 0) {
            const placeholders = reviewIds.map(() => '?').join(',');
            reviewAttachments = db.query(`SELECT * FROM attachments WHERE review_id IN (${placeholders})`, reviewIds);
        }

        // Delete physical files
        const allAttachments = [...submissionAttachments, ...reviewAttachments];
        allAttachments.forEach(att => {
            const filepath = path.join(uploadsDir, att.filepath);
            if (fs.existsSync(filepath)) {
                try {
                    fs.unlinkSync(filepath);
                } catch (err) {
                    console.error(`Failed to delete file ${att.filepath}:`, err);
                }
            }
        });

        // Delete from database (in order due to foreign keys)
        db.run('DELETE FROM attachments WHERE submission_id = ?', [submissionId]);

        if (reviewIds.length > 0) {
            const placeholders = reviewIds.map(() => '?').join(',');
            db.run(`DELETE FROM attachments WHERE review_id IN (${placeholders})`, reviewIds);
        }

        db.run('DELETE FROM reviews WHERE submission_id = ?', [submissionId]);
        db.run('DELETE FROM watchlist WHERE submission_id = ?', [submissionId]);
        db.run('DELETE FROM submissions WHERE id = ?', [submissionId]);

        res.json({ success: true, message: 'Submission deleted successfully' });
    } catch (error) {
        console.error('Delete error:', error);
        res.status(500).json({ error: error.message });
    }
});

// ===== REVIEW ROUTES =====

app.post('/api/reviews', upload.array('attachments', 5), async (req, res) => {
    try {
        const {
            submissionId,
            reviewerName,
            confidenceLevel,
            technicalScore,
            fundamentalsScore,
            themeScore,
            sectorScore,
            finalScore,
            reasoning,
            entryRange,
            sellRange,
            timeHorizon
        } = req.body;

        console.log('\n=== REVIEW SUBMISSION DEBUG ===');
        console.log('Received reviewerName:', JSON.stringify(reviewerName));
        console.log('Received submissionId:', submissionId);

        // Get submission to check reviewer constraints
        const submission = db.query('SELECT * FROM submissions WHERE id = ?', [submissionId]);

        if (submission.length === 0) {
            return res.status(404).json({ error: 'Submission not found' });
        }

        // Check if user is the submitter
        if (submission[0].submitter_name === reviewerName) {
            return res.status(400).json({ error: 'Cannot review your own submission' });
        }

        // Check if user already reviewed this
        const existing = db.query(
            'SELECT * FROM reviews WHERE submission_id = ? AND reviewer_name = ?',
            [submissionId, reviewerName]
        );

        if (existing.length > 0) {
            return res.status(400).json({ error: 'You already reviewed this submission' });
        }

        // Get reviewer user ID from the database
        console.log('Looking up user with full_name:', JSON.stringify(reviewerName));
        const reviewer = db.query('SELECT id, full_name FROM users WHERE full_name = ?', [reviewerName]);
        console.log('Query result:', reviewer);

        const reviewerId = reviewer.length > 0 ? reviewer[0].id : 1;
        console.log('Assigned reviewerId:', reviewerId);

        // DEBUG: Show all users in database
        const allUsers = db.query('SELECT id, full_name FROM users');
        console.log('All users in database:', allUsers);

        console.log('About to INSERT review with:');
        console.log('  - submission_id:', submissionId);
        console.log('  - reviewer_id:', reviewerId);
        console.log('  - reviewer_name:', reviewerName);
        console.log('================================\n');

        // Insert review with all scoring fields
        db.run(`
            INSERT INTO reviews (
                submission_id, reviewer_id, reviewer_name,
                confidence_level, technical_score, fundamentals_score, theme_score, sector_score,
                final_score, reasoning, entry_range, sell_range, time_horizon
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        `, [
            submissionId,
            reviewerId,
            reviewerName,
            confidenceLevel,
            technicalScore,
            fundamentalsScore,
            themeScore,
            sectorScore,
            finalScore,
            reasoning,
            entryRange || null,
            sellRange || null,
            timeHorizon
        ]);

        const reviewId = db.getLastInsertId();

        // Handle file attachments
        if (req.files && req.files.length > 0) {
            req.files.forEach(file => {
                db.run(`
                    INSERT INTO attachments (review_id, filename, filepath, file_type, file_size)
                    VALUES (?, ?, ?, ?, ?)
                `, [
                    reviewId,
                    file.originalname,
                    file.filename,
                    file.mimetype,
                    file.size
                ]);
            });
        }

        // Check review progress
        const reviews = db.query('SELECT * FROM reviews WHERE submission_id = ?', [submissionId]);
        const reviewsNeeded = 3; // All 3 other team members

        // Send Discord notification
        await discord.sendReviewCompleteNotification(submission[0].ticker, reviewerName,
            reviews.length,
            reviewsNeeded
        );

        // If all reviews complete, send final notification
        if (reviews.length >= reviewsNeeded) {
            const avgConfidence = reviews.reduce((sum, r) => sum + r.confidence_level, 0) / reviews.length;

            // Update submission status
            db.run('UPDATE submissions SET status = ? WHERE id = ?', ['under_review', submissionId]);

            await discord.sendAllReviewsCompleteNotification(
                submission[0].ticker,
                avgConfidence
            );
        }

        res.json({ success: true, reviewId });
    } catch (error) {
        res.status(500).json({ error: error.message });
    }
});

app.get('/api/pending-reviews', (req, res) => {
    try {
        // Get all submissions (no auth filtering - Option A approach)
        const pending = db.query(`
            SELECT s.*
            FROM submissions s
            ORDER BY s.created_at DESC
        `);

        res.json(pending);
    } catch (error) {
        res.status(500).json({ error: error.message });
    }
});

// ===== WATCHLIST ROUTES =====

app.post('/api/watchlist/approve/:id', (req, res) => {
    try {
        const submissionId = req.params.id;

        // Calculate average confidence
        const reviews = db.query('SELECT * FROM reviews WHERE submission_id = ?', [submissionId]);
        const submission = db.query('SELECT * FROM submissions WHERE id = ?', [submissionId]);

        if (submission.length === 0) {
            return res.status(404).json({ error: 'Submission not found' });
        }

        // Include submitter's confidence in average
        const allConfidenceLevels = [submission[0].confidence_level, ...reviews.map(r => r.confidence_level)];
        const avgConfidence = allConfidenceLevels.reduce((sum, c) => sum + c, 0) / allConfidenceLevels.length;

        // Add to watchlist
        db.run(`
            INSERT INTO watchlist (submission_id, avg_confidence)
            VALUES (?, ?)
        `, [submissionId, avgConfidence]);

        // Update submission status
        db.run(`
            UPDATE submissions SET status = 'approved', approved_at = CURRENT_TIMESTAMP
            WHERE id = ?
        `, [submissionId]);

        res.json({ success: true });
    } catch (error) {
        res.status(500).json({ error: error.message });
    }
});

app.get('/api/watchlist', (req, res) => {
    try {
        const watchlist = db.query(`
            SELECT
                w.*,
                s.ticker,
                s.company_name,
                s.submitter_name,
                s.sector,
                s.time_horizon,
                s.created_at,
                s.final_score,
                (
                    SELECT AVG(r.final_score)
                    FROM reviews r
                    WHERE r.submission_id = s.id
                ) as avg_review_score
            FROM watchlist w
            JOIN submissions s ON w.submission_id = s.id
            ORDER BY w.avg_confidence DESC, w.added_at DESC
        `);

        // Calculate combined average (submission + reviews)
        const watchlistWithScores = watchlist.map(item => {
            const submissionScore = parseFloat(item.final_score) || 0;
            const avgReviewScore = parseFloat(item.avg_review_score) || 0;
            const reviewCount = db.query('SELECT COUNT(*) as count FROM reviews WHERE submission_id = ?', [item.submission_id])[0].count;

            // Average of submission score and all review scores
            const avg_final_score = reviewCount > 0
                ? (submissionScore + (avgReviewScore * reviewCount)) / (reviewCount + 1)
                : submissionScore;

            return {
                ...item,
                avg_final_score: avg_final_score.toFixed(2)
            };
        });

        res.json(watchlistWithScores);
    } catch (error) {
        res.status(500).json({ error: error.message });
    }
});

// ===== FILE DOWNLOAD ROUTE =====

app.get('/api/files/:filename', (req, res) => {
    const filename = req.params.filename;
    const filepath = path.join(uploadsDir, filename);

    if (fs.existsSync(filepath)) {
        res.download(filepath);
    } else {
        res.status(404).json({ error: 'File not found' });
    }
});

// ===== START SERVER =====

async function startServer() {
    try {
        await db.initDatabase();
        console.log('Database initialized');

        app.listen(PORT, () => {
            console.log(`\n✅ Watchlist Management Server running on http://localhost:${PORT}`);
            console.log(`\n📊 Default login credentials:`);
            console.log(`   Username: paxton | sam | alex | garett`);
            console.log(`   Password: OLCTeam2025\n`);
        });
    } catch (error) {
        console.error('Failed to start server:', error);
        process.exit(1);
    }
}

startServer();
