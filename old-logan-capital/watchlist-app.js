// Auto-detect API URL based on environment
const API_URL = window.location.hostname === 'localhost' || window.location.hostname === '127.0.0.1'
    ? 'http://localhost:3000/api'
    : 'https://api.oldlogancapital.com/api';

let currentSubmissionId = null;

// Initialize app
document.addEventListener('DOMContentLoaded', () => {
    loadSubmissions();
    loadPendingReviews();
    loadWatchlist();
    setupEventListeners();
});

function setupEventListeners() {
    // Submit form
    document.getElementById('submitForm').addEventListener('submit', handleSubmit);

    // File input display
    document.getElementById('attachments').addEventListener('change', displayFileList);

    // Ticker input for sector auto-population
    document.getElementById('ticker').addEventListener('blur', autoPopulateSector);
    document.getElementById('ticker').addEventListener('input', debounce(() => {
        const ticker = document.getElementById('ticker').value.trim();
        if (ticker.length >= 1) {
            autoPopulateSector();
        }
    }, 500));
}

// Debounce function to limit API calls
function debounce(func, wait) {
    let timeout;
    return function executedFunction(...args) {
        const later = () => {
            clearTimeout(timeout);
            func(...args);
        };
        clearTimeout(timeout);
        timeout = setTimeout(later, wait);
    };
}


// ===== TAB SWITCHING =====

function switchTab(tabName, event) {
    // Update tab buttons
    document.querySelectorAll('.tab').forEach(tab => tab.classList.remove('active'));

    // Only try to set active on event.target if event exists
    if (event && event.target) {
        event.target.classList.add('active');
    } else {
        // Find and activate the correct tab button by matching the onclick attribute
        const tabs = document.querySelectorAll('.tab');
        tabs.forEach(tab => {
            const onclickAttr = tab.getAttribute('onclick');
            if (onclickAttr && onclickAttr.includes(`'${tabName}'`)) {
                tab.classList.add('active');
            }
        });
    }

    // Update tab content
    document.querySelectorAll('.tab-content').forEach(content => content.classList.remove('active'));
    document.getElementById(tabName + 'Tab').classList.add('active');

    // Load data if needed
    if (tabName === 'dashboard') loadSubmissions();
    if (tabName === 'reviews') loadPendingReviews();
    if (tabName === 'watchlist') loadWatchlist();
}

// ===== SUBMISSIONS =====

async function loadSubmissions() {
    try {
        const response = await fetch(`${API_URL}/submissions`, {
            
        });

        if (!response.ok) throw new Error('Failed to load submissions');

        let submissions = await response.json();

        // Sort based on selection
        const sortBy = document.getElementById('sortSelect').value;
        submissions = sortSubmissions(submissions, sortBy);

        const tbody = document.getElementById('submissionsTableBody');
        tbody.innerHTML = '';

        if (submissions.length === 0) {
            tbody.innerHTML = '<tr><td colspan="6" style="text-align: center; color: var(--text-muted);">No submissions yet</td></tr>';
            return;
        }

        submissions.forEach(sub => {
            const row = document.createElement('tr');
            row.className = 'clickable-row';
            row.onclick = () => viewSubmission(sub.id);

            const avgScore = sub.avg_final_score ? parseFloat(sub.avg_final_score).toFixed(2) : '-';
            const scoreDisplay = sub.avg_final_score ? `${avgScore}/10` : 'Pending';

            row.innerHTML = `
                <td><strong>${sub.ticker}</strong></td>
                <td>${sub.company_name}</td>
                <td>${sub.review_count}/3</td>
                <td><strong>${scoreDisplay}</strong></td>
                <td><span class="status-badge status-${sub.status}">${sub.status.replace('_', ' ')}</span></td>
                <td>${new Date(sub.created_at).toLocaleDateString()}</td>
            `;

            tbody.appendChild(row);
        });
    } catch (error) {
        console.error('Error loading submissions:', error);
    }
}

function sortSubmissions(submissions, sortBy) {
    switch (sortBy) {
        case 'score':
            return submissions.sort((a, b) => (b.avg_final_score || 0) - (a.avg_final_score || 0));
        case 'date':
            return submissions.sort((a, b) => new Date(b.created_at) - new Date(a.created_at));
        case 'status':
            return submissions.sort((a, b) => a.status.localeCompare(b.status));
        case 'ticker':
            return submissions.sort((a, b) => a.ticker.localeCompare(b.ticker));
        default:
            return submissions;
    }
}

async function viewSubmission(id) {
    try {
        const response = await fetch(`${API_URL}/submissions/${id}`, {

        });

        if (!response.ok) throw new Error('Failed to load submission details');

        const submission = await response.json();

        // DEBUG: Log what backend is returning
        console.log('=== BACKEND RESPONSE DEBUG ===');
        console.log('Full submission object:', submission);
        console.log('Submission scores:', {
            confidence: submission.confidence_level,
            technical: submission.technical_score,
            fundamentals: submission.fundamentals_score,
            theme: submission.theme_score,
            sector: submission.sector_score,
            final: submission.final_score
        });
        if (submission.reviews && submission.reviews.length > 0) {
            console.log('Review 0 scores:', {
                confidence: submission.reviews[0].confidence_level,
                technical: submission.reviews[0].technical_score,
                final: submission.reviews[0].final_score
            });
        }
        console.log('============================');

        currentSubmissionId = id;

        let html = `
            <h2>${submission.ticker} - ${submission.company_name}</h2>

            <div class="detail-row">
                <span class="detail-label">Status:</span>
                <span><span class="status-badge status-${submission.status}">${submission.status.replace('_', ' ')}</span></span>
            </div>
        `;

        // Only show details if status is NOT "submitted"
        if (submission.status !== 'submitted') {
            html += `
                ${submission.reviewsComplete ? `
                <div class="detail-row">
                    <span class="detail-label">Submitted by:</span>
                    <span>${submission.submitter_name}</span>
                </div>
                ` : ''}

                <div class="detail-row">
                    <span class="detail-label">Final Score:</span>
                    <span><strong>${submission.final_score ? parseFloat(submission.final_score).toFixed(2) : 'Pending'}/10</strong></span>
                </div>

                <div class="detail-row">
                    <span class="detail-label">Entry Range:</span>
                    <span>${submission.entry_range || 'N/A'}</span>
                </div>

                <div class="detail-row">
                    <span class="detail-label">Sell Range:</span>
                    <span>${submission.sell_range || 'N/A'}</span>
                </div>

                <div class="detail-row">
                    <span class="detail-label">Time Horizon:</span>
                    <span>${submission.time_horizon}</span>
                </div>

                <div class="detail-row">
                    <span class="detail-label">Sector:</span>
                    <span>${submission.sector || 'N/A'}</span>
                </div>

                <div style="margin-top: 2rem;">
                    <h3 style="margin-bottom: 1rem;">Investment Thesis</h3>
                    <p style="line-height: 1.8; white-space: pre-wrap;">${submission.reasoning}</p>
                </div>
            `;

            // Show attachments if any
            if (submission.attachments && submission.attachments.length > 0) {
                html += `
                    <div style="margin-top: 2rem;">
                        <h3 style="margin-bottom: 1rem;">Attachments</h3>
                        <ul class="attachment-list">
                            ${submission.attachments.map(att => `
                                <li><a href="${API_URL}/files/${att.filepath}" target="_blank">📎 ${att.filename}</a></li>
                            `).join('')}
                        </ul>
                    </div>
                `;
            }
        }

        // Add delete button
        html += `
            <div style="margin-top: 2rem; padding-top: 2rem; border-top: 2px solid var(--border);">
                <button class="btn btn-danger" onclick="deleteSubmission(${id})" style="background: var(--danger); border-color: var(--danger);">
                    🗑️ Delete Submission
                </button>
            </div>
        `;

        // Show reviews if complete
        if (submission.reviewsComplete && submission.reviews.length > 0) {
            // Calculate team averages for all scores using safe getters
            const allScores = [
                {
                    name: submission.submitter_name,
                    confidence: getSafeScore(submission.confidence_level),
                    technical: getSafeScore(submission.technical_score),
                    fundamentals: getSafeScore(submission.fundamentals_score),
                    theme: getSafeScore(submission.theme_score),
                    sector: getSafeScore(submission.sector_score),
                    final_score: getSafeScore(submission.final_score)
                },
                ...submission.reviews.map(r => ({
                    name: r.reviewer_name,
                    confidence: getSafeScore(r.confidence_level),
                    technical: getSafeScore(r.technical_score),
                    fundamentals: getSafeScore(r.fundamentals_score),
                    theme: getSafeScore(r.theme_score),
                    sector: getSafeScore(r.sector_score),
                    final_score: getSafeScore(r.final_score)
                }))
            ];

            const avgScores = {
                confidence: (allScores.reduce((sum, s) => sum + s.confidence, 0) / allScores.length).toFixed(2),
                technical: (allScores.reduce((sum, s) => sum + s.technical, 0) / allScores.length).toFixed(2),
                fundamentals: (allScores.reduce((sum, s) => sum + s.fundamentals, 0) / allScores.length).toFixed(2),
                theme: (allScores.reduce((sum, s) => sum + s.theme, 0) / allScores.length).toFixed(2),
                sector: (allScores.reduce((sum, s) => sum + s.sector, 0) / allScores.length).toFixed(2),
                final: (allScores.reduce((sum, s) => sum + s.final_score, 0) / allScores.length).toFixed(2)
            };

            html += `
                <div style="margin-top: 2rem;">
                    <h3 style="margin-bottom: 1rem;">Team Scores Summary</h3>

                    <!-- Team Averages Card -->
                    <div style="background: rgba(42, 90, 74, 0.1); border: 2px solid var(--forest); padding: 1.5rem; margin-bottom: 2rem;">
                        <h4 style="margin-bottom: 1rem; color: var(--forest);">Team Average Scores</h4>
                        <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 1rem;">
                            <div><strong>Final Score:</strong> ${avgScores.final}/10</div>
                            <div><strong>Confidence:</strong> ${avgScores.confidence}/10</div>
                            <div><strong>Technical:</strong> ${avgScores.technical}/10</div>
                            <div><strong>Fundamentals:</strong> ${avgScores.fundamentals}/10</div>
                            <div><strong>Theme:</strong> ${avgScores.theme}/5</div>
                            <div><strong>Sector:</strong> ${avgScores.sector}/5</div>
                        </div>
                    </div>

                    <h3 style="margin-bottom: 1rem;">Individual Reviews</h3>
                    <div class="review-grid">
            `;

            // Show submitter's scores first
            const subScores = allScores[0]; // Already using safe getters
            html += `
                <div class="review-card">
                    <h4>${submission.submitter_name} <span style="color: var(--gold); font-size: 0.8rem;">(Submitter)</span></h4>
                    <div class="detail-row">
                        <span class="detail-label">Final Score:</span>
                        <span><strong>${subScores.final_score.toFixed(2)}/10</strong></span>
                    </div>
                    <div class="detail-row">
                        <span class="detail-label">Confidence:</span>
                        <span>${subScores.confidence}/10</span>
                    </div>
                    <div class="detail-row">
                        <span class="detail-label">Technical:</span>
                        <span>${subScores.technical}/10</span>
                    </div>
                    <div class="detail-row">
                        <span class="detail-label">Fundamentals:</span>
                        <span>${subScores.fundamentals}/10</span>
                    </div>
                    <div class="detail-row">
                        <span class="detail-label">Theme:</span>
                        <span>${subScores.theme}/5</span>
                    </div>
                    <div class="detail-row">
                        <span class="detail-label">Sector:</span>
                        <span>${subScores.sector}/5</span>
                    </div>
                    <div class="detail-row">
                        <span class="detail-label">Entry Range:</span>
                        <span>${submission.entry_range || 'N/A'}</span>
                    </div>
                    <div class="detail-row">
                        <span class="detail-label">Sell Range:</span>
                        <span>${submission.sell_range || 'N/A'}</span>
                    </div>
                    <div class="detail-row">
                        <span class="detail-label">Time Horizon:</span>
                        <span>${submission.time_horizon}</span>
                    </div>
                </div>
            `;

            // Show reviewer scores
            submission.reviews.forEach((review, index) => {
                const revScore = allScores[index + 1]; // +1 because allScores[0] is the submitter
                html += `
                    <div class="review-card">
                        <h4>${review.reviewer_name}</h4>
                        <div class="detail-row">
                            <span class="detail-label">Final Score:</span>
                            <span><strong>${revScore.final_score.toFixed(2)}/10</strong></span>
                        </div>
                        <div class="detail-row">
                            <span class="detail-label">Confidence:</span>
                            <span>${revScore.confidence}/10</span>
                        </div>
                        <div class="detail-row">
                            <span class="detail-label">Technical:</span>
                            <span>${revScore.technical}/10</span>
                        </div>
                        <div class="detail-row">
                            <span class="detail-label">Fundamentals:</span>
                            <span>${revScore.fundamentals}/10</span>
                        </div>
                        <div class="detail-row">
                            <span class="detail-label">Theme:</span>
                            <span>${revScore.theme}/5</span>
                        </div>
                        <div class="detail-row">
                            <span class="detail-label">Sector:</span>
                            <span>${revScore.sector}/5</span>
                        </div>
                        <div class="detail-row">
                            <span class="detail-label">Entry Range:</span>
                            <span>${review.entry_range || 'N/A'}</span>
                        </div>
                        <div class="detail-row">
                            <span class="detail-label">Sell Range:</span>
                            <span>${review.sell_range || 'N/A'}</span>
                        </div>
                        <div class="detail-row">
                            <span class="detail-label">Time Horizon:</span>
                            <span>${review.time_horizon}</span>
                        </div>
                        <div style="margin-top: 1rem;">
                            <strong>Analysis:</strong>
                            <p style="margin-top: 0.5rem; line-height: 1.6; white-space: pre-wrap;">${review.reasoning}</p>
                        </div>
                    </div>
                `;
            });

            html += `</div></div>`;

            // Add approve button if all reviews complete and not yet approved
            if (submission.status === 'under_review') {
                html += `
                    <div style="margin-top: 2rem;">
                        <button class="btn btn-success" onclick="approveForWatchlist(${id})">
                            ✓ Approve for Watchlist
                        </button>
                    </div>
                `;
            }
        } else {
            html += `
                <div style="margin-top: 2rem; padding: 1.5rem; background: rgba(201, 169, 98, 0.1); border: 1px solid var(--warning);">
                    <p style="color: var(--text-muted);">
                        <strong>Reviews in Progress:</strong> ${submission.review_count || 0}/3 reviews completed.
                        Results will be visible to everyone once all reviews are finished.
                    </p>
                </div>
            `;
        }

        document.getElementById('submissionDetail').innerHTML = html;
        document.getElementById('submissionModal').classList.add('active');
    } catch (error) {
        console.error('Error viewing submission:', error);
        alert('Failed to load submission details');
    }
}

function closeSubmissionModal() {
    document.getElementById('submissionModal').classList.remove('active');
}

// Auto-populate sector based on ticker
async function autoPopulateSector() {
    const ticker = document.getElementById('ticker').value.trim().toUpperCase();
    const sectorInput = document.getElementById('sector');

    if (!ticker) {
        sectorInput.value = '';
        sectorInput.placeholder = 'Enter ticker first';
        return;
    }

    try {
        sectorInput.value = 'Loading...';
        sectorInput.readOnly = true;

        console.log(`Fetching sector for ticker: ${ticker} from ${API_URL}/ticker-info/${ticker}`);

        const response = await fetch(`${API_URL}/ticker-info/${ticker}`);

        if (response.status === 404) {
            // Endpoint doesn't exist or ticker not found
            const errorText = await response.text();
            console.error('Sector fetch 404:', errorText);

            if (errorText.includes('Cannot GET') || errorText.includes('Not Found')) {
                console.warn('⚠️ Backend endpoint /api/ticker-info/:ticker not implemented yet');
                sectorInput.value = '';
                sectorInput.placeholder = 'Backend endpoint not ready - enter manually';
            } else {
                sectorInput.value = '';
                sectorInput.placeholder = 'Ticker not found - enter manually';
            }
            sectorInput.readOnly = false;
            sectorInput.style.background = 'rgba(255, 255, 255, 0.8)';
            return;
        }

        if (!response.ok) {
            throw new Error(`HTTP ${response.status}: ${response.statusText}`);
        }

        const data = await response.json();
        console.log('Sector data received:', data);

        sectorInput.value = data.sector || 'N/A';
        sectorInput.readOnly = false;
        sectorInput.style.background = 'rgba(255, 255, 255, 0.8)';
        console.log(`✓ Sector auto-populated: ${data.sector}`);
    } catch (error) {
        console.error('Error fetching sector:', error);
        sectorInput.value = '';
        sectorInput.placeholder = 'Unable to auto-populate, enter manually';
        sectorInput.readOnly = false;
        sectorInput.style.background = 'rgba(255, 255, 255, 0.8)';
    }
}

// Helper function to safely get score value with fallback
function getSafeScore(value, defaultValue = 0) {
    if (value === null || value === undefined || value === '') {
        return defaultValue;
    }
    const parsed = parseFloat(value);
    return isNaN(parsed) ? defaultValue : parsed;
}

// Calculate final score from all scoring inputs
function calculateFinalScore(scores) {
    // scores object should contain: confidence, technical, fundamentals, theme, sector
    const finalScore = (
        parseInt(scores.confidence) +
        parseInt(scores.technical) +
        parseInt(scores.fundamentals) +
        parseInt(scores.theme) +
        parseInt(scores.sector)
    ) / 5;
    return finalScore.toFixed(2);
}

async function handleSubmit(e) {
    e.preventDefault();

    const formData = new FormData();
    formData.append('ticker', document.getElementById('ticker').value);
    formData.append('submitterName', document.getElementById('submitterName').value);
    formData.append('companyName', document.getElementById('companyName').value);
    formData.append('reasoning', document.getElementById('reasoning').value);
    formData.append('entryRange', document.getElementById('entryRange').value);
    formData.append('sellRange', document.getElementById('sellRange').value);
    formData.append('timeHorizon', document.getElementById('timeHorizon').value);
    formData.append('sector', document.getElementById('sector').value);

    // Add all scoring fields
    formData.append('confidenceLevel', document.getElementById('confidenceLevel').value);
    formData.append('technicalScore', document.getElementById('technicalScore').value);
    formData.append('fundamentalsScore', document.getElementById('fundamentalsScore').value);
    formData.append('themeScore', document.getElementById('themeScore').value);
    formData.append('sectorScore', document.getElementById('sectorScore').value);

    // Calculate and append final score
    const finalScore = calculateFinalScore({
        confidence: document.getElementById('confidenceLevel').value,
        technical: document.getElementById('technicalScore').value,
        fundamentals: document.getElementById('fundamentalsScore').value,
        theme: document.getElementById('themeScore').value,
        sector: document.getElementById('sectorScore').value
    });
    formData.append('finalScore', finalScore);

    // Add files
    const files = document.getElementById('attachments').files;
    for (let i = 0; i < files.length; i++) {
        formData.append('attachments', files[i]);
    }

    try {
        const response = await fetch(`${API_URL}/submissions`, {
            method: 'POST',
            
            body: formData
        });

        if (!response.ok) {
            const error = await response.json();
            throw new Error(error.error || 'Submission failed');
        }

        document.getElementById('submitSuccess').textContent = 'Ticker submitted successfully! Notification sent to team.';
        document.getElementById('submitSuccess').classList.remove('hidden');
        document.getElementById('submitError').classList.add('hidden');
        document.getElementById('submitForm').reset();
        document.getElementById('fileList').innerHTML = '';

        // Refresh dashboard
        loadSubmissions();

        // Switch to dashboard after 2 seconds
        setTimeout(() => {
            switchTab('dashboard');
            document.getElementById('submitSuccess').classList.add('hidden');
        }, 2000);
    } catch (error) {
        document.getElementById('submitError').textContent = error.message;
        document.getElementById('submitError').classList.remove('hidden');
        document.getElementById('submitSuccess').classList.add('hidden');
    }
}

function displayFileList() {
    const files = document.getElementById('attachments').files;
    const fileList = document.getElementById('fileList');

    if (files.length === 0) {
        fileList.innerHTML = '';
        return;
    }

    const fileNames = Array.from(files).map(f => f.name).join(', ');
    fileList.innerHTML = `Selected: ${fileNames}`;
}

// ===== REVIEWS =====

async function loadPendingReviews() {
    try {
        const response = await fetch(`${API_URL}/pending-reviews`, {
            
        });

        if (!response.ok) throw new Error('Failed to load pending reviews');

        const pending = await response.json();
        const container = document.getElementById('pendingReviewsList');

        if (pending.length === 0) {
            container.innerHTML = '<p style="text-align: center; color: var(--text-muted); padding: 2rem;">No pending reviews</p>';
            return;
        }

        container.innerHTML = pending.map(sub => `
            <div class="card" style="margin-bottom: 1rem;">
                <div style="display: flex; justify-content: space-between; align-items: center;">
                    <div style="flex: 1;">
                        <h3 style="margin-bottom: 0.5rem;">${sub.ticker} - ${sub.company_name}</h3>
                        <p style="color: var(--text-muted); font-size: 0.9rem;">Submitted on ${new Date(sub.created_at).toLocaleDateString()}</p>
                    </div>
                    <div style="display: flex; gap: 0.5rem;">
                        <button class="btn" onclick="startReview(${sub.id})">Start Review</button>
                        <button class="btn btn-secondary" onclick="viewSubmission(${sub.id})">View Details</button>
                    </div>
                </div>
            </div>
        `).join('');
    } catch (error) {
        console.error('Error loading pending reviews:', error);
    }
}

async function startReview(submissionId) {
    try {
        const response = await fetch(`${API_URL}/submissions/${submissionId}`, {
            
        });

        if (!response.ok) throw new Error('Failed to load submission');

        const submission = await response.json();

        const html = `
            <h2>Review: ${submission.ticker} - ${submission.company_name}</h2>
            <p style="margin-bottom: 2rem; color: var(--text-muted);">
                Complete your independent review. Your analysis will remain hidden until all team members finish their reviews.
            </p>

            <div style="padding: 1.5rem; background: rgba(255, 255, 255, 0.8); border: 1px solid var(--border); margin-bottom: 2rem;">
                <h3 style="margin-bottom: 1rem;">Original Submission</h3>
                <div class="detail-row">
                    <span class="detail-label">Ticker:</span>
                    <span><strong>${submission.ticker}</strong></span>
                </div>
                <div class="detail-row">
                    <span class="detail-label">Company:</span>
                    <span>${submission.company_name}</span>
                </div>
                <div class="detail-row">
                    <span class="detail-label">Sector:</span>
                    <span>${submission.sector || 'N/A'}</span>
                </div>
                <p style="margin-top: 1rem;"><strong>Note:</strong> Submitter identity and full analysis are hidden to prevent bias.</p>
            </div>

            <form id="reviewForm" onsubmit="submitReview(event, ${submissionId})">
                <div class="form-group">
                    <label for="reviewerName">Your Name *</label>
                    <select id="reviewerName" required>
                        <option value="">Select your name</option>
                        <option value="Paxton Thompson">Paxton</option>
                        <option value="Alex Evenson">Alex</option>
                        <option value="Garett Lake">Garett</option>
                        <option value="Sam Thoresen">Sam</option>
                    </select>
                </div>

                <div class="form-group">
                    <label for="reviewReasoning">Your Analysis / Reasoning *</label>
                    <textarea id="reviewReasoning" required placeholder="Provide your independent analysis of this investment opportunity..."></textarea>
                </div>

                <div class="form-group">
                    <label for="reviewEntryRange">Entry Range *</label>
                    <input type="text" id="reviewEntryRange" required placeholder="e.g., $45-50">
                </div>

                <div class="form-group">
                    <label for="reviewSellRange">Sell Range *</label>
                    <input type="text" id="reviewSellRange" required placeholder="e.g., $75-85">
                </div>

                <div class="form-group">
                    <label for="reviewTimeHorizon">Your Time Horizon *</label>
                    <select id="reviewTimeHorizon" required>
                        <option value="">Select time horizon</option>
                        <option value="Short">Short Term (0-6 months)</option>
                        <option value="Medium">Medium Term (6-12 months)</option>
                        <option value="Long">Long Term (12+ months)</option>
                    </select>
                </div>

                <h3 style="margin: 2rem 0 1rem 0; font-size: 1.3rem; border-bottom: 2px solid var(--border); padding-bottom: 0.5rem;">Your Scoring (All scores required)</h3>

                <!-- Core Scores -->
                <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 1.5rem; margin-bottom: 1.5rem;">
                    <div class="form-group">
                        <label for="reviewConfidence">Confidence Level (1-10) *</label>
                        <select id="reviewConfidence" required>
                            <option value="">Select</option>
                            <option value="1">1</option>
                            <option value="2">2</option>
                            <option value="3">3</option>
                            <option value="4">4</option>
                            <option value="5">5</option>
                            <option value="6">6</option>
                            <option value="7">7</option>
                            <option value="8">8</option>
                            <option value="9">9</option>
                            <option value="10">10</option>
                        </select>
                    </div>

                    <div class="form-group">
                        <label for="reviewTechnical">Technical Analysis (1-10) *</label>
                        <select id="reviewTechnical" required>
                            <option value="">Select</option>
                            <option value="1">1</option>
                            <option value="2">2</option>
                            <option value="3">3</option>
                            <option value="4">4</option>
                            <option value="5">5</option>
                            <option value="6">6</option>
                            <option value="7">7</option>
                            <option value="8">8</option>
                            <option value="9">9</option>
                            <option value="10">10</option>
                        </select>
                    </div>

                    <div class="form-group">
                        <label for="reviewFundamentals">Fundamentals (1-10) *</label>
                        <select id="reviewFundamentals" required>
                            <option value="">Select</option>
                            <option value="1">1</option>
                            <option value="2">2</option>
                            <option value="3">3</option>
                            <option value="4">4</option>
                            <option value="5">5</option>
                            <option value="6">6</option>
                            <option value="7">7</option>
                            <option value="8">8</option>
                            <option value="9">9</option>
                            <option value="10">10</option>
                        </select>
                    </div>

                    <div class="form-group">
                        <label for="reviewTheme">Theme (1-5) *</label>
                        <select id="reviewTheme" required>
                            <option value="">Select</option>
                            <option value="1">1</option>
                            <option value="2">2</option>
                            <option value="3">3</option>
                            <option value="4">4</option>
                            <option value="5">5</option>
                        </select>
                    </div>

                    <div class="form-group">
                        <label for="reviewSectorScore">Sector (1-5) *</label>
                        <select id="reviewSectorScore" required>
                            <option value="">Select</option>
                            <option value="1">1</option>
                            <option value="2">2</option>
                            <option value="3">3</option>
                            <option value="4">4</option>
                            <option value="5">5</option>
                        </select>
                    </div>
                </div>

                <div class="form-group">
                    <label for="reviewAttachments">Supporting Documents (Optional)</label>
                    <div class="file-input-wrapper">
                        <input type="file" id="reviewAttachments" multiple accept=".pdf,.png,.jpg,.jpeg,.xlsx,.xls,.doc,.docx,.csv">
                        <label for="reviewAttachments" class="file-input-label">Choose files</label>
                    </div>
                    <div id="reviewFileList" class="file-list"></div>
                </div>

                <div style="display: flex; gap: 1rem;">
                    <button type="submit" class="btn">Submit Review</button>
                    <button type="button" class="btn btn-secondary" onclick="closeReviewModal()">Cancel</button>
                </div>
            </form>
        `;

        document.getElementById('reviewFormContainer').innerHTML = html;
        document.getElementById('reviewModal').classList.add('active');

        // Setup file list display for review attachments
        document.getElementById('reviewAttachments').addEventListener('change', () => {
            const files = document.getElementById('reviewAttachments').files;
            const fileList = document.getElementById('reviewFileList');
            if (files.length > 0) {
                const fileNames = Array.from(files).map(f => f.name).join(', ');
                fileList.innerHTML = `Selected: ${fileNames}`;
            } else {
                fileList.innerHTML = '';
            }
        });
    } catch (error) {
        console.error('Error starting review:', error);
        alert('Failed to load submission for review');
    }
}

async function submitReview(e, submissionId) {
    e.preventDefault();

    const formData = new FormData();
    formData.append('submissionId', submissionId);
    formData.append('reviewerName', document.getElementById('reviewerName').value);
    formData.append('reasoning', document.getElementById('reviewReasoning').value);
    formData.append('entryRange', document.getElementById('reviewEntryRange').value);
    formData.append('sellRange', document.getElementById('reviewSellRange').value);
    formData.append('timeHorizon', document.getElementById('reviewTimeHorizon').value);

    // Add all scoring fields
    formData.append('confidenceLevel', document.getElementById('reviewConfidence').value);
    formData.append('technicalScore', document.getElementById('reviewTechnical').value);
    formData.append('fundamentalsScore', document.getElementById('reviewFundamentals').value);
    formData.append('themeScore', document.getElementById('reviewTheme').value);
    formData.append('sectorScore', document.getElementById('reviewSectorScore').value);

    // Calculate and append final score
    const finalScore = calculateFinalScore({
        confidence: document.getElementById('reviewConfidence').value,
        technical: document.getElementById('reviewTechnical').value,
        fundamentals: document.getElementById('reviewFundamentals').value,
        theme: document.getElementById('reviewTheme').value,
        sector: document.getElementById('reviewSectorScore').value
    });
    formData.append('finalScore', finalScore);

    // Add files
    const files = document.getElementById('reviewAttachments').files;
    for (let i = 0; i < files.length; i++) {
        formData.append('attachments', files[i]);
    }

    try {
        const response = await fetch(`${API_URL}/reviews`, {
            method: 'POST',
            
            body: formData
        });

        if (!response.ok) {
            const error = await response.json();
            throw new Error(error.error || 'Review submission failed');
        }

        alert('Review submitted successfully! Notification sent to team.');
        closeReviewModal();
        loadPendingReviews();
        loadSubmissions();
    } catch (error) {
        alert(error.message);
    }
}

function closeReviewModal() {
    document.getElementById('reviewModal').classList.remove('active');
}

// ===== WATCHLIST =====

async function loadWatchlist() {
    try {
        const response = await fetch(`${API_URL}/watchlist`, {
            
        });

        if (!response.ok) throw new Error('Failed to load watchlist');

        const watchlist = await response.json();
        const tbody = document.getElementById('watchlistTableBody');
        tbody.innerHTML = '';

        if (watchlist.length === 0) {
            tbody.innerHTML = '<tr><td colspan="7" style="text-align: center; color: var(--text-muted);">No approved items yet</td></tr>';
            return;
        }

        watchlist.forEach(item => {
            const row = document.createElement('tr');
            row.className = 'clickable-row';
            row.onclick = () => viewSubmission(item.submission_id);

            const finalScore = item.avg_final_score ? parseFloat(item.avg_final_score).toFixed(2) : 'N/A';

            row.innerHTML = `
                <td><strong>${item.ticker}</strong></td>
                <td>${item.company_name}</td>
                <td>${item.submitter_name}</td>
                <td><strong>${finalScore}/10</strong></td>
                <td>${item.sector || 'N/A'}</td>
                <td>${item.time_horizon}</td>
                <td>${new Date(item.added_at).toLocaleDateString()}</td>
            `;

            tbody.appendChild(row);
        });
    } catch (error) {
        console.error('Error loading watchlist:', error);
    }
}

async function approveForWatchlist(submissionId) {
    if (!confirm('Are you sure you want to approve this ticker for the watchlist?')) {
        return;
    }

    try {
        const response = await fetch(`${API_URL}/watchlist/approve/${submissionId}`, {
            method: 'POST',

        });

        if (!response.ok) throw new Error('Approval failed');

        alert('Ticker approved and added to watchlist!');
        closeSubmissionModal();
        loadSubmissions();
        loadWatchlist();
    } catch (error) {
        alert('Failed to approve ticker: ' + error.message);
    }
}

async function deleteSubmission(submissionId) {
    if (!confirm('Are you sure you want to permanently delete this submission? This action cannot be undone.')) {
        return;
    }

    try {
        const response = await fetch(`${API_URL}/submissions/${submissionId}`, {
            method: 'DELETE',

        });

        if (!response.ok) throw new Error('Delete failed');

        alert('Submission deleted successfully!');
        closeSubmissionModal();
        loadSubmissions();
        loadPendingReviews();
        loadWatchlist();
    } catch (error) {
        alert('Failed to delete submission: ' + error.message);
    }
}
