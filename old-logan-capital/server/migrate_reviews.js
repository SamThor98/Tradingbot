const initSqlJs = require('sql.js');
const fs = require('fs');
const path = require('path');

const dataDir = process.env.RENDER ? '/opt/render/project/src/data' : __dirname;
const dbPath = path.join(dataDir, 'watchlist.db');

async function migrate() {
    try {
        const SQL = await initSqlJs();

        console.log('📂 Data directory:', dataDir);
        console.log('📂 Database path:', dbPath);

        if (!fs.existsSync(dbPath)) {
            console.log('ℹ️  No database found - will be created with new schema on first run');
            return;
        }

        const buffer = fs.readFileSync(dbPath);
        const db = new SQL.Database(buffer);

        console.log('🔄 Starting migration: Update reviews table UNIQUE constraint');

        // Check if migration is needed by checking the schema
        const schema = db.exec("SELECT sql FROM sqlite_master WHERE type='table' AND name='reviews'");
        if (schema.length > 0 && schema[0].values.length > 0) {
            const createTableSQL = schema[0].values[0][0];
            console.log('Current schema:', createTableSQL);

            if (createTableSQL.includes('UNIQUE(submission_id, reviewer_name)')) {
                console.log('✅ Migration already applied - skipping');
                db.close();
                return;
            }
        }

        // Get existing reviews before dropping table
        const reviews = db.exec('SELECT * FROM reviews');
        const reviewCount = reviews.length > 0 ? reviews[0].values.length : 0;
        console.log(`📊 Found ${reviewCount} existing reviews to preserve`);

        // Drop old reviews table
        console.log('🗑️  Dropping old reviews table...');
        db.run('DROP TABLE IF EXISTS reviews');

        // Recreate with UNIQUE on (submission_id, reviewer_name)
        console.log('🔨 Creating new reviews table with updated constraint...');
        db.run(`
            CREATE TABLE reviews (
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

        // Restore reviews if any existed
        if (reviews.length > 0 && reviews[0].values.length > 0) {
            console.log(`♻️  Restoring ${reviews[0].values.length} reviews...`);
            reviews[0].values.forEach((row, index) => {
                try {
                    db.run(
                        `INSERT INTO reviews (id, submission_id, reviewer_id, reviewer_name, confidence_level, reasoning, price_target, time_horizon, sector, created_at)
                         VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
                        row
                    );
                } catch (err) {
                    console.error(`⚠️  Failed to restore review ${index + 1}:`, err.message);
                }
            });
            console.log(`✅ Restored ${reviews[0].values.length} reviews`);
        }

        // Save database
        console.log('💾 Saving database...');
        const data = db.export();
        const newBuffer = Buffer.from(data);
        fs.writeFileSync(dbPath, newBuffer);

        console.log('✅ Migration complete: UNIQUE constraint now on (submission_id, reviewer_name)');
        db.close();

        // Verify the migration
        const newDb = new SQL.Database(fs.readFileSync(dbPath));
        const newSchema = newDb.exec("SELECT sql FROM sqlite_master WHERE type='table' AND name='reviews'");
        if (newSchema.length > 0) {
            console.log('✅ Verified new schema:', newSchema[0].values[0][0]);
        }
        newDb.close();

    } catch (error) {
        console.error('❌ Migration failed:', error);
        console.error(error.stack);
        process.exit(1); // Exit with error code to fail deployment
    }
}

migrate().catch(err => {
    console.error('❌ Fatal migration error:', err);
    process.exit(1);
});
