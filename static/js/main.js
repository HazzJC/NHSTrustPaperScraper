// NHS Board Paper Scraper JavaScript

document.addEventListener('DOMContentLoaded', function() {
    // Elements
    const runCrawlerBtn = document.getElementById('runCrawlerBtn');
    const crawlerStatus = document.getElementById('crawlerStatus');
    const showNewOnlyCheckbox = document.getElementById('showNewOnly');
    const papersTableBody = document.getElementById('papersTableBody');
    const testUrlsBtn = document.getElementById('testUrlsBtn');
    const scrapeOnlyBtn = document.getElementById('scrapeOnlyBtn');
    const testUrlsStatus = document.getElementById('testUrlsStatus');
    const urlInput = document.getElementById('urlInput');

    // Run crawler button
    if (runCrawlerBtn) {
        runCrawlerBtn.addEventListener('click', function() {
            // Disable button and show status
            runCrawlerBtn.disabled = true;
            crawlerStatus.classList.remove('d-none');

            // Call the API to run the crawler
            fetch('/run-crawler', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                }
            })
            .then(response => response.json())
            .then(data => {
                // Update the table with new results
                updatePapersTable(data.results.board_papers);

                // Show success message
                alert('Crawler completed successfully!');

                // Reload the page to show updated results
                window.location.reload();
            })
            .catch(error => {
                console.error('Error running crawler:', error);
                alert('Error running crawler. Check the console for details.');
            })
            .finally(() => {
                // Re-enable button and hide status
                runCrawlerBtn.disabled = false;
                crawlerStatus.classList.add('d-none');
            });
        });
    }

    // Test specific URLs button
    if (testUrlsBtn) {
        testUrlsBtn.addEventListener('click', function() {
            // Get the URLs from the input
            const urlsText = urlInput.value.trim();

            if (!urlsText) {
                alert('Please enter at least one URL to test.');
                return;
            }

            // Parse URLs with @ prefix or regular line-by-line format
            const urls = parseUrlInput(urlsText);

            if (urls.length === 0) {
                alert('Please enter at least one valid URL to test.');
                return;
            }

            // Disable button and show status
            testUrlsBtn.disabled = true;
            testUrlsStatus.classList.remove('d-none');

            // Call the API to test the URLs
            const payload = { urls: urls };
            console.log("Sending payload to server:", payload);

            fetch('/test-specific-urls', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify(payload)
            })
            .then(response => {
                if (!response.ok) {
                    throw new Error(`HTTP error! status: ${response.status}`);
                }
                return response.json();
            })
            .then(data => {
                if (data.status === 'success') {
                    // Update the table with new results
                    updatePapersTable(data.results.board_papers);

                    // Show success message
                    alert(`Test completed successfully! Found ${data.results.board_papers.length} papers.`);

                    // Reload the page to show updated results
                    window.location.reload();
                } else {
                    // Show error message in the status area
                    testUrlsStatus.querySelector('span:last-child').textContent = `Error: ${data.message}`;
                    testUrlsStatus.classList.remove('alert-info');
                    testUrlsStatus.classList.add('alert-danger');
                }
            })
            .catch(error => {
                console.error('Error testing URLs:', error);
                // Show error in the status area
                testUrlsStatus.querySelector('span:last-child').textContent = `Error testing URLs: ${error.message}`;
                testUrlsStatus.classList.remove('alert-info');
                testUrlsStatus.classList.add('alert-danger');
            })
            .finally(() => {
                // Re-enable button but don't hide status if there was an error
                testUrlsBtn.disabled = false;
                if (!testUrlsStatus.classList.contains('alert-danger')) {
                    testUrlsStatus.classList.add('d-none');
                }
                testUrlsStatus.querySelector('span:last-child').textContent = 'Testing URLs... This may take a while.';
            });
        });
    }

    // Scrape Only button
    if (scrapeOnlyBtn) {
        scrapeOnlyBtn.addEventListener('click', function() {
            // Get the URLs from the input
            const urlsText = urlInput.value.trim();

            if (!urlsText) {
                alert('Please enter at least one URL to test.');
                return;
            }

            // Parse URLs with @ prefix or regular line-by-line format
            const urls = parseUrlInput(urlsText);

            if (urls.length === 0) {
                alert('Please enter at least one valid URL to test.');
                return;
            }

            // Disable buttons and show status
            scrapeOnlyBtn.disabled = true;
            testUrlsBtn.disabled = true;
            testUrlsStatus.classList.remove('d-none');
            testUrlsStatus.querySelector('span:last-child').textContent = 'Scraping URLs... This may take a while.';

            // Call the API to test the URLs with scrape_only flag
            const payload = {
                urls: urls,
                scrape_only: true  // This flag tells the backend to skip PDF analysis
            };

            fetch('/test-specific-urls', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify(payload)
            })
            .then(response => {
                if (!response.ok) {
                    throw new Error(`HTTP error! status: ${response.status}`);
                }
                return response.json();
            })
            .then(data => {
                if (data.status === 'success') {
                    // Update the table with new results
                    updatePapersTable(data.results.board_papers);

                    // Show success message
                    alert(`Scraping completed successfully! Found ${data.results.board_papers.length} papers.`);

                    // Reload the page to show updated results
                    window.location.reload();
                } else {
                    // Show error message in the status area
                    testUrlsStatus.querySelector('span:last-child').textContent = `Error: ${data.message}`;
                    testUrlsStatus.classList.remove('alert-info');
                    testUrlsStatus.classList.add('alert-danger');
                }
            })
            .catch(error => {
                console.error('Error scraping URLs:', error);
                // Show error in the status area
                testUrlsStatus.querySelector('span:last-child').textContent = `Error scraping URLs: ${error.message}`;
                testUrlsStatus.classList.remove('alert-info');
                testUrlsStatus.classList.add('alert-danger');
            })
            .finally(() => {
                // Re-enable buttons but don't hide status if there was an error
                scrapeOnlyBtn.disabled = false;
                testUrlsBtn.disabled = false;
                if (!testUrlsStatus.classList.contains('alert-danger')) {
                    testUrlsStatus.classList.add('d-none');
                }
                testUrlsStatus.querySelector('span:last-child').textContent = 'Testing URLs... This may take a while.';
            });
        });
    }

    // Function to parse URL input text that may contain @ prefixes
    function parseUrlInput(inputText) {
        console.log("Parsing URL input:", inputText);

        // First check if the input contains @ symbols (for the special format)
        if (inputText.includes('@')) {
            // Find all URLs with @ prefix
            const urlMatches = inputText.match(/@https?:\/\/[^\s]+/g);

            if (urlMatches) {
                console.log("Found @ prefixed URLs:", urlMatches);
                // Remove the @ prefix from each URL
                const parsedUrls = urlMatches.map(url => url.substring(1).trim());
                console.log("Parsed URLs:", parsedUrls);
                return parsedUrls;
            }
            console.log("No valid URLs found with @ prefix");
            return [];
        } else {
            // Regular line-by-line format (backward compatibility)
            const parsedUrls = inputText.split('\n')
                .map(url => url.trim())
                .filter(url => url.length > 0);
            console.log("Parsed line-by-line URLs:", parsedUrls);
            return parsedUrls;
        }
    }

    // Show new papers only checkbox
    if (showNewOnlyCheckbox) {
        showNewOnlyCheckbox.addEventListener('change', function() {
            const paperRows = document.querySelectorAll('.paper-row');

            if (this.checked) {
                // Show only new papers
                paperRows.forEach(row => {
                    if (!row.classList.contains('new-paper')) {
                        row.classList.add('hidden');
                    }
                });
            } else {
                // Show all papers
                paperRows.forEach(row => {
                    row.classList.remove('hidden');
                });
            }
        });
    }

    // Function to update the papers table
    function updatePapersTable(papers) {
        // Clear the table
        papersTableBody.innerHTML = '';

        if (papers && papers.length > 0) {
            // Sort papers chronologically by sort_date (yyyy-mm format)
            papers.sort((a, b) => {
                const dateA = a.sort_date || '9999-99';
                const dateB = b.sort_date || '9999-99';
                return dateA.localeCompare(dateB);
            });

            // Add papers to the table
            papers.forEach(paper => {
                const row = document.createElement('tr');
                row.className = `paper-row ${paper.is_new ? 'table-success new-paper' : ''}`;

                // Extract year and month for display
                let formattedDate = paper.date || '';

                // Try to improve the date display using sort_date if available
                if (paper.sort_date && paper.sort_date !== '9999-99') {
                    const parts = paper.sort_date.split('-');
                    if (parts.length === 2) {
                        const year = parts[0];
                        const monthNum = parseInt(parts[1]);

                        if (monthNum > 0 && monthNum <= 12) {
                            const monthNames = [
                                'January', 'February', 'March', 'April', 'May', 'June',
                                'July', 'August', 'September', 'October', 'November', 'December'
                            ];
                            formattedDate = `${monthNames[monthNum-1]} ${year}`;
                        } else {
                            formattedDate = year;
                        }
                    }
                }

                row.innerHTML = `
                    <td>${paper.title}</td>
                    <td>${paper.organization}</td>
                    <td>${paper.org_type}</td>
                    <td>${formattedDate}</td>
                    <td>
                        <a href="${paper.url}" target="_blank" class="btn btn-sm btn-primary">View</a>
                    </td>
                `;

                papersTableBody.appendChild(row);
            });
        } else {
            // Show no papers message
            const row = document.createElement('tr');
            row.innerHTML = `
                <td colspan="5" class="text-center">No board papers found from 2024 onwards.</td>
            `;
            papersTableBody.appendChild(row);
        }
    }
});

// ---------------------------------------------------------------------------
// ScrapeDashboard — wires the new "Scrape Board Papers" control card
// ---------------------------------------------------------------------------
(function ScrapeDashboard() {
    'use strict';

    let currentJobId = null;
    let eventSource = null;

    const startBtn      = document.getElementById('startScrapeBtn');
    const cancelBtn     = document.getElementById('cancelScrapeBtn');
    const logPanel      = document.getElementById('logPanel');
    const logOutput     = document.getElementById('logOutput');
    const clearLogBtn   = document.getElementById('clearLogBtn');
    const scrapeSummary = document.getElementById('scrapeSummary');
    const scrapeProgress= document.getElementById('scrapeProgress');
    const trustSelect   = document.getElementById('trustSelect');
    const selectAllBtn  = document.getElementById('selectAllTrustsBtn');
    const clearAllBtn   = document.getElementById('clearAllTrustsBtn');
    const trustSpinner  = document.getElementById('trustLoadingSpinner');

    if (!startBtn) return; // guard if card not present

    // ---- Populate trust list -----------------------------------------------
    function loadTrusts() {
        if (trustSpinner) trustSpinner.classList.remove('d-none');
        fetch('/scrape/trusts')
            .then(r => r.json())
            .then(trusts => {
                trustSelect.innerHTML = '';
                trusts.forEach(t => {
                    const opt = document.createElement('option');
                    opt.value = t.name;
                    opt.textContent = t.name;
                    trustSelect.appendChild(opt);
                });
            })
            .catch(() => {
                trustSelect.innerHTML = '<option disabled>Failed to load trusts</option>';
            })
            .finally(() => {
                if (trustSpinner) trustSpinner.classList.add('d-none');
            });
    }

    selectAllBtn && selectAllBtn.addEventListener('click', () => {
        Array.from(trustSelect.options).forEach(o => o.selected = true);
    });
    clearAllBtn && clearAllBtn.addEventListener('click', () => {
        Array.from(trustSelect.options).forEach(o => o.selected = false);
    });

    // ---- Log helpers ---------------------------------------------------------
    function appendLog(text) {
        logOutput.textContent += text + '\n';
        logOutput.scrollTop = logOutput.scrollHeight;
    }

    clearLogBtn && clearLogBtn.addEventListener('click', () => {
        logOutput.textContent = '';
    });

    // ---- Collect form state --------------------------------------------------
    function getSelectedTypes() {
        const types = [];
        ['typeBoard','typeSupp','typeStrategy','typeDigital'].forEach(id => {
            const el = document.getElementById(id);
            if (el && el.checked) types.push(el.value);
        });
        return types.length ? types : ['board'];
    }

    function getSelectedTrusts() {
        const selected = Array.from(trustSelect.selectedOptions).map(o => o.value);
        return selected.length ? selected : null; // null = all 47
    }

    // ---- SSE handler ---------------------------------------------------------
    function handleSseMessage(evt) {
        let msg;
        try { msg = JSON.parse(evt.data); } catch { return; }

        const { event, trust, index, total, found, downloaded, error, message } = msg;

        if (event === 'trust_start') {
            appendLog(`[${index}/${total}] ${trust}`);
            if (scrapeProgress) scrapeProgress.textContent = `${index} / ${total} trusts`;
        } else if (event === 'trust_done') {
            appendLog(`  ✓ found ${found || 0}, downloaded ${downloaded || 0}`);
        } else if (event === 'trust_error') {
            appendLog(`  ✗ ERROR: ${error || 'unknown error'}`);
        } else if (event === 'candidate_found') {
            appendLog(`  → [${msg.report_type}] ${msg.date} ${msg.url}`);
        } else if (event === 'done') {
            appendLog(`\nDone. ${msg.completed}/${msg.total} trusts processed, ${msg.downloads} file(s) downloaded.`);
            setRunning(false);
            scrapeSummary.classList.remove('d-none');
            scrapeSummary.textContent =
                `Scrape complete: ${msg.completed} trusts processed, ${msg.downloads} file(s) downloaded.`;
            if (eventSource) { eventSource.close(); eventSource = null; }
        } else if (event === 'cancelled') {
            appendLog('\nJob cancelled.');
            setRunning(false);
            if (eventSource) { eventSource.close(); eventSource = null; }
        }
    }

    // ---- UI state toggle -----------------------------------------------------
    function setRunning(running) {
        startBtn.disabled = running;
        cancelBtn.classList.toggle('d-none', !running);
        if (!running && scrapeProgress) scrapeProgress.textContent = '';
    }

    // ---- Start ---------------------------------------------------------------
    startBtn.addEventListener('click', () => {
        const types = getSelectedTypes();
        const trustNames = getSelectedTrusts();
        const allMatches = document.getElementById('allMatchesChk').checked;
        const dryRun = document.getElementById('dryRunChk').checked;
        const limitPerType = parseInt(document.getElementById('limitPerType').value, 10) || 1;

        scrapeSummary.classList.add('d-none');
        logPanel.classList.remove('d-none');
        logOutput.textContent = '';
        setRunning(true);
        appendLog(`Starting scrape: types=[${types.join(', ')}], trusts=${trustNames ? trustNames.length + ' selected' : 'all 47'}, dry_run=${dryRun}`);

        fetch('/scrape/start', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                trust_names: trustNames,
                types: types,
                all_matches: allMatches,
                limit_per_type: limitPerType,
                dry_run: dryRun,
            }),
        })
        .then(r => r.json())
        .then(data => {
            if (data.error) {
                appendLog('Error: ' + data.error);
                setRunning(false);
                return;
            }
            currentJobId = data.job_id;
            appendLog(`Job ${currentJobId} started (${data.trust_count} trusts)`);

            eventSource = new EventSource(`/scrape/stream/${currentJobId}`);
            eventSource.onmessage = handleSseMessage;
            eventSource.onerror = () => {
                appendLog('[connection lost]');
                setRunning(false);
                eventSource.close();
                eventSource = null;
            };
        })
        .catch(err => {
            appendLog('Request failed: ' + err);
            setRunning(false);
        });
    });

    // ---- Cancel --------------------------------------------------------------
    cancelBtn.addEventListener('click', () => {
        if (!currentJobId) return;
        fetch(`/scrape/cancel/${currentJobId}`, { method: 'DELETE' })
            .catch(() => {});
        appendLog('Cancellation requested…');
    });

    // ---- Init ----------------------------------------------------------------
    loadTrusts();
}());
