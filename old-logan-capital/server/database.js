const initSqlJs = require('sql.js');
const fs = require('fs');
const path = require('path');

// Use persistent disk path on Render, local path otherwise
const dataDir = process.env.RENDER ? '/opt/render/project/src/data' : __dirname;
const dbPath = path.join(dataDir, 'watchlist.db');

// Ensure data directory exists
if (!fs.existsSync(dataDir)) {
    fs.mkdirSync(dataDir, { recursive: true });
}

let db = null;

async function initDatabase() {
    const SQL = await initSqlJs();

    // Load existing database or create new one
    if (fs.existsSync(dbPath)) {
        const buffer = fs.readFileSync(dbPath);
        db = new SQL.Database(buffer);
        // Run migration to add scoring columns if they don't exist
        migrateAddScoringColumns();
    } else {
        db = new SQL.Database();
        createTables();
        seedUsers();
        saveDatabase();
    }

    return db;
}

function createTables() {
    // Users table
    db.run(`
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            full_name TEXT NOT NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    `);

    // Submissions table
    db.run(`
        CREATE TABLE IF NOT EXISTS submissions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker TEXT NOT NULL,
            company_name TEXT NOT NULL,
            submitter_id INTEGER NOT NULL,
            submitter_name TEXT NOT NULL,
            confidence_level INTEGER NOT NULL,
            reasoning TEXT NOT NULL,
            price_target TEXT,
            time_horizon TEXT NOT NULL,
            sector TEXT,
            status TEXT DEFAULT 'submitted',
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            approved_at DATETIME,
            FOREIGN KEY (submitter_id) REFERENCES users(id)
        )
    `);

    // Reviews table
    db.run(`
        CREATE TABLE IF NOT EXISTS reviews (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            submission_id INTEGER NOT NULL,
            reviewer_id INTEGER NOT NULL,
            reviewer_name TEXT NOT NULL,
            confidence_level INTEGER NOT NULL,
            reasoning TEXT NOT NULL,
            price_target TEXT,
            time_horizon TEXT NOT NULL,
            sector TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (submission_id) REFERENCES submissions(id),
            FOREIGN KEY (reviewer_id) REFERENCES users(id),
            UNIQUE(submission_id, reviewer_name)
        )
    `);

    // Attachments table
    db.run(`
        CREATE TABLE IF NOT EXISTS attachments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            submission_id INTEGER,
            review_id INTEGER,
            filename TEXT NOT NULL,
            filepath TEXT NOT NULL,
            file_type TEXT,
            file_size INTEGER,
            uploaded_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (submission_id) REFERENCES submissions(id),
            FOREIGN KEY (review_id) REFERENCES reviews(id)
        )
    `);

    // Watchlist table (approved items)
    db.run(`
        CREATE TABLE IF NOT EXISTS watchlist (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            submission_id INTEGER NOT NULL UNIQUE,
            avg_confidence REAL,
            added_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (submission_id) REFERENCES submissions(id)
        )
    `);
}

function seedUsers() {
    const bcrypt = require('bcryptjs');

    // Default password: "OLCTeam2025" - should be changed after first login
    const defaultPassword = bcrypt.hashSync('OLCTeam2025', 10);

    const users = [
        { username: 'paxton', full_name: 'Paxton Thompson', password: defaultPassword },
        { username: 'sam', full_name: 'Sam Thoresen', password: defaultPassword },
        { username: 'alex', full_name: 'Alex Evenson', password: defaultPassword },
        { username: 'garett', full_name: 'Garett Lake', password: defaultPassword }
    ];

    users.forEach(user => {
        db.run(
            'INSERT INTO users (username, full_name, password) VALUES (?, ?, ?)',
            [user.username, user.full_name, user.password]
        );
    });
}

function migrateAddScoringColumns() {
    console.log('\n=== Running Scoring Columns Migration ===');

    // Check if columns already exist by trying to select them
    try {
        const test = query('SELECT technical_score FROM submissions LIMIT 1');
        console.log('✓ Scoring columns already exist, skipping migration');
    } catch (error) {
        // Columns don't exist, proceed with migration
        console.log('Adding scoring columns to database...');
    }

    try {
        // Add columns to submissions table
        const submissionColumns = [
            'technical_score INTEGER',
            'fundamentals_score INTEGER',
            'theme_score INTEGER',
            'sector_score INTEGER',
            'canslim_c INTEGER',
            'canslim_a INTEGER',
            'canslim_n INTEGER',
            'canslim_s INTEGER',
            'canslim_l INTEGER',
            'canslim_i INTEGER',
            'canslim_m INTEGER',
            'final_score REAL',
            'entry_range TEXT',
            'sell_range TEXT'
        ];

        submissionColumns.forEach(column => {
            const columnName = column.split(' ')[0];
            try {
                db.run(`ALTER TABLE submissions ADD COLUMN ${column}`);
                console.log(`✓ Added ${columnName} to submissions`);
            } catch (error) {
                if (!error.message.includes('duplicate column')) {
                    console.error(`✗ Error adding ${columnName}:`, error.message);
                }
            }
        });

        // Add columns to reviews table
        const reviewColumns = [
            'technical_score INTEGER',
            'fundamentals_score INTEGER',
            'theme_score INTEGER',
            'sector_score INTEGER',
            'canslim_c INTEGER',
            'canslim_a INTEGER',
            'canslim_n INTEGER',
            'canslim_s INTEGER',
            'canslim_l INTEGER',
            'canslim_i INTEGER',
            'canslim_m INTEGER',
            'final_score REAL',
            'entry_range TEXT',
            'sell_range TEXT'
        ];

        reviewColumns.forEach(column => {
            const columnName = column.split(' ')[0];
            try {
                db.run(`ALTER TABLE reviews ADD COLUMN ${column}`);
                console.log(`✓ Added ${columnName} to reviews`);
            } catch (error) {
                if (!error.message.includes('duplicate column')) {
                    console.error(`✗ Error adding ${columnName}:`, error.message);
                }
            }
        });

        // Save the updated database
        saveDatabase();

        console.log('✓ Migration complete - all scoring columns added\n');
    } catch (error) {
        console.error('✗ Migration failed:', error);
        throw error;
    }
}

function saveDatabase() {
    if (db) {
        const data = db.export();
        const buffer = Buffer.from(data);
        fs.writeFileSync(dbPath, buffer);
    }
}

function getDatabase() {
    return db;
}

// Run a query and return results
function query(sql, params = []) {
    try {
        const stmt = db.prepare(sql);
        stmt.bind(params);

        const results = [];
        while (stmt.step()) {
            results.push(stmt.getAsObject());
        }
        stmt.free();

        return results;
    } catch (error) {
        console.error('Query error:', error);
        throw error;
    }
}

// Run an insert/update/delete query
function run(sql, params = []) {
    try {
        db.run(sql, params);
        saveDatabase();
        return { success: true };
    } catch (error) {
        console.error('Run error:', error);
        throw error;
    }
}

// Get last inserted ID
function getLastInsertId() {
    const result = query('SELECT last_insert_rowid() as id');
    return result[0].id;
}

module.exports = {
    initDatabase,
    getDatabase,
    saveDatabase,
    query,
    run,
    getLastInsertId
};
