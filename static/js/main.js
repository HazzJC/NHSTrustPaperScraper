// NHS Evidence Scraper — main.js
'use strict';

// ---------------------------------------------------------------------------
// EvidenceScraper — unified multi-source controller
// ---------------------------------------------------------------------------
(function EvidenceScraper() {

    const TYPE_LABELS = {
        board:                    'Board Papers',
        supplementary:            'Supplementary',
        strategy:                 'Strategic Reporting',
        digital_strategy:         'Digital Strategy',
        quality_account:          'Quality Account',
        annual_report:            'Annual Report',
        cqc_report:               'CQC Report',
        joint_forward_plan:       'Joint Forward Plan',
        icb_mh_strategy:          'ICB MH Strategy',
        integrated_care_strategy: 'Integrated Care Strategy',
    };

    const TRUST_TYPES = [
        ['typeBoard',          'board'],
        ['typeQualityAccount', 'quality_account'],
        ['typeAnnualReport',   'annual_report'],
        ['typeStrategy',       'strategy'],
        ['typeDigital',        'digital_strategy'],
        ['typeSupp',           'supplementary'],
        ['typeCqcReport',      'cqc_report'],
    ];

    const ICB_TYPES = [
        ['typeJFP',         'joint_forward_plan'],
        ['typeMHStrategy',  'icb_mh_strategy'],
        ['typeICS',         'integrated_care_strategy'],
    ];

    // Per-source job state
    const jobs = {
        trust:    { jobId: null, es: null, total: 0, completed: 0, done: false },
        icb:      { jobId: null, es: null, total: 0, completed: 0, done: false },
        national: { jobId: null, es: null, total: 0, fetched: 0, done: false },
    };

    // Track which sources are active in current run
    let activeSources = [];
    // Track last trust/ICB job IDs for CSV export
    let lastTrustJobId = null;
    let lastICBJobId   = null;

    const startBtn       = document.getElementById('startAllBtn');
    const globalStatus   = document.getElementById('globalStatus');
    const progressSection= document.getElementById('progressSection');
    const resultsSection = document.getElementById('resultsSection');
    const scrapeSummary  = document.getElementById('scrapeSummary');
    const resultsHeaderRow = document.getElementById('resultsHeaderRow');
    const resultsBody    = document.getElementById('resultsBody');
    const exportCsvBtn   = document.getElementById('exportCsvBtn');

    if (!startBtn) return;

    // ── Source checkbox toggles ─────────────────────────────────────────────
    document.querySelectorAll('.source-checkbox').forEach(cb => {
        cb.addEventListener('change', () => toggleSourcePanel(cb.value, cb.checked));
    });

    function toggleSourcePanel(source, active) {
        const panelIds = { trust: 'trustPanel', icb: 'icbPanel', national: 'nationalPanel' };
        const panel = document.getElementById(panelIds[source]);
        if (!panel) return;
        panel.classList.toggle('d-none', !active);
        panel.classList.toggle('panel-active', active);
    }
    // Ensure initial state matches checked state
    document.querySelectorAll('.source-checkbox').forEach(cb => toggleSourcePanel(cb.value, cb.checked));

    // ── Type checkbox → show/hide date filter row ───────────────────────────
    [...TRUST_TYPES, ...ICB_TYPES].forEach(([cbId, type]) => {
        const cb = document.getElementById(cbId);
        if (!cb) return;
        cb.addEventListener('change', () => {
            const row = document.getElementById('dfRow_' + type);
            if (row) row.classList.toggle('d-none', !cb.checked);
        });
    });

    // ── Load org lists ──────────────────────────────────────────────────────
    function loadOrgList(endpoint, selectId, spinnerId) {
        const sel = document.getElementById(selectId);
        const spinner = document.getElementById(spinnerId);
        if (!sel) return;
        if (spinner) spinner.classList.remove('d-none');
        fetch(endpoint)
            .then(r => r.json())
            .then(items => {
                sel.innerHTML = '';
                items.forEach(t => {
                    const opt = document.createElement('option');
                    opt.value = t.name;
                    opt.textContent = t.name;
                    sel.appendChild(opt);
                });
            })
            .catch(() => { sel.innerHTML = '<option disabled>Failed to load</option>'; })
            .finally(() => { if (spinner) spinner.classList.add('d-none'); });
    }

    loadOrgList('/scrape/trusts', 'trustSelect', 'trustLoadingSpinner');
    loadOrgList('/scrape/icbs',   'icbSelect',   'icbLoadingSpinner');

    document.getElementById('selectAllTrustsBtn') && document.getElementById('selectAllTrustsBtn').addEventListener('click', () => {
        Array.from(document.getElementById('trustSelect').options).forEach(o => o.selected = true);
    });
    document.getElementById('clearAllTrustsBtn') && document.getElementById('clearAllTrustsBtn').addEventListener('click', () => {
        Array.from(document.getElementById('trustSelect').options).forEach(o => o.selected = false);
    });
    document.getElementById('selectAllICBsBtn') && document.getElementById('selectAllICBsBtn').addEventListener('click', () => {
        Array.from(document.getElementById('icbSelect').options).forEach(o => o.selected = true);
    });
    document.getElementById('clearAllICBsBtn') && document.getElementById('clearAllICBsBtn').addEventListener('click', () => {
        Array.from(document.getElementById('icbSelect').options).forEach(o => o.selected = false);
    });

    // ── Load national datasets ──────────────────────────────────────────────
    fetch('/national/sources')
        .then(r => r.json())
        .then(sources => {
            const list = document.getElementById('nationalSourceList');
            if (!list) return;
            list.innerHTML = '';
            sources.forEach(s => {
                const div = document.createElement('div');
                div.className = 'form-check';
                div.innerHTML = `<input class="form-check-input national-source-cb" type="checkbox"
                                        id="ns_${s.key}" value="${s.key}" checked>
                                 <label class="form-check-label small" for="ns_${s.key}">${s.display_name}</label>`;
                list.appendChild(div);
            });
        })
        .catch(() => {
            const list = document.getElementById('nationalSourceList');
            if (list) list.innerHTML = '<span class="text-danger small">Failed to load dataset list.</span>';
        });

    // ── Log helpers per source ──────────────────────────────────────────────
    const logEls = { trust: 'logTrust', icb: 'logICB', national: 'logNational' };
    function appendLog(source, text) {
        const el = document.getElementById(logEls[source]);
        if (!el) return;
        el.textContent += text + '\n';
        el.scrollTop = el.scrollHeight;
    }

    // ── Badge + progress helpers ────────────────────────────────────────────
    function setBadge(source, text, cls) {
        const badge = document.getElementById('badge' + cap(source));
        if (!badge) return;
        badge.textContent = text;
        badge.className = 'badge me-2 ' + cls;
    }
    function setProgress(source, done, total) {
        const bar   = document.getElementById('bar'   + cap(source));
        const count = document.getElementById('count' + cap(source));
        const pct = total > 0 ? Math.round(done / total * 100) : 0;
        if (bar)   bar.style.width = pct + '%';
        if (count) count.textContent = total > 0 ? `${done} / ${total}` : '';
    }
    function cap(s) { return s.charAt(0).toUpperCase() + s.slice(1); }

    function markSlotDone(source, success) {
        const slot = document.getElementById('progress' + cap(source));
        if (!slot) return;
        slot.classList.toggle('slot-done',  success);
        slot.classList.toggle('slot-error', !success);
    }

    // ── Collect form settings ───────────────────────────────────────────────
    function getSharedSettings() {
        return {
            parallel_trusts: parseInt(document.getElementById('parallelTrusts').value, 10) || 5,
            crawl_delay:     parseFloat(document.getElementById('crawlDelay').value)       || 0.5,
            limit_per_type:  parseInt(document.getElementById('limitPerType').value, 10)  || 1,
            dry_run:         document.getElementById('dryRunChk').checked,
            all_matches:     document.getElementById('allMatchesChk').checked,
            max_pages:       parseInt(document.getElementById('maxPages').value, 10)       || 60,
            ignore_cache:    document.getElementById('ignoreCacheChk').checked,
        };
    }

    function getSelectedTypes(typePairs) {
        const types = [];
        typePairs.forEach(([cbId, val]) => {
            const cb = document.getElementById(cbId);
            if (cb && cb.checked) types.push(val);
        });
        return types.length ? types : [typePairs[0][1]];
    }

    function getDateFilters(typePairs) {
        const filters = {};
        typePairs.forEach(([cbId, type]) => {
            const cb = document.getElementById(cbId);
            if (cb && cb.checked) {
                const inp = document.getElementById('dateFilter_' + type);
                filters[type] = inp ? (parseInt(inp.value, 10) || 0) : 0;
            }
        });
        return filters;
    }

    function getSelectedOrgs(selectId) {
        const sel = document.getElementById(selectId);
        if (!sel) return null;
        const selected = Array.from(sel.selectedOptions).map(o => o.value);
        return selected.length ? selected : null;
    }

    // ── Check all done → re-enable button ──────────────────────────────────
    function checkAllDone() {
        const allDone = activeSources.every(s => jobs[s].done);
        if (allDone) {
            startBtn.disabled = false;
            globalStatus.textContent = 'All sources complete.';
            // refresh failure log after run
            if (typeof loadFailureLog === 'function') loadFailureLog();
        }
    }

    // ── SSE: trust / icb ───────────────────────────────────────────────────
    function openScrapeStream(source, jobId, orgLabel) {
        const es = new EventSource(`/scrape/stream/${jobId}`);
        jobs[source].es = es;

        es.onmessage = evt => {
            let msg;
            try { msg = JSON.parse(evt.data); } catch { return; }
            const { event, trust, index, total, found, downloaded, error } = msg;

            if (event === 'trust_start') {
                jobs[source].total = total;
                setProgress(source, index - 1, total);
                appendLog(source, `[${index}/${total}] ${trust}`);
            } else if (event === 'trust_done') {
                jobs[source].completed++;
                setProgress(source, index || jobs[source].completed, jobs[source].total);
                appendLog(source, `  ✓ found ${found || 0}, downloaded ${downloaded || 0}`);
            } else if (event === 'trust_error') {
                appendLog(source, `  ✗ ERROR: ${error || 'unknown'}`);
            } else if (event === 'candidate_found') {
                appendLog(source, `  → [${msg.report_type}] ${msg.date} ${msg.url}`);
            } else if (event === 'candidate_skipped') {
                appendLog(source, `  ↩ already exists: ${msg.url}`);
            } else if (event === 'trust_retry') {
                appendLog(source, `  ↺ cached pages empty — running full crawl`);
            } else if (event === 'done') {
                const failCount = msg.failures || 0;
                appendLog(source, `\nDone. ${msg.completed}/${msg.total} ${orgLabel}s processed, ${msg.downloads} downloaded.${failCount > 0 ? ` ${failCount} failure(s).` : ''}`);
                setProgress(source, msg.total || jobs[source].total, msg.total || jobs[source].total);
                setBadge(source, failCount > 0 ? 'Done (with failures)' : 'Done', failCount > 0 ? 'bg-warning text-dark' : 'bg-success');
                markSlotDone(source, failCount === 0);
                es.close();
                jobs[source].done = true;
                // show results section for trust/ICB
                showResultsAfterRun(source, jobId, msg);
                checkAllDone();
            } else if (event === 'cancelled') {
                appendLog(source, '\nCancelled.');
                setBadge(source, 'Cancelled', 'bg-secondary');
                es.close();
                jobs[source].done = true;
                checkAllDone();
            }
        };

        es.onerror = () => {
            appendLog(source, '[connection lost]');
            setBadge(source, 'Error', 'bg-danger');
            markSlotDone(source, false);
            es.close();
            jobs[source].done = true;
            checkAllDone();
        };
    }

    // ── SSE: national ──────────────────────────────────────────────────────
    function openNationalStream(jobId, totalDatasets) {
        const es = new EventSource(`/national/stream/${jobId}`);
        jobs.national.es = es;
        jobs.national.total = totalDatasets;
        let fetched = 0;

        es.addEventListener('fetch_start', evt => {
            const d = JSON.parse(evt.data);
            appendLog('national', `↓  ${d.name}`);
            setBadge('national', 'Fetching…', 'bg-info text-dark');
        });
        es.addEventListener('fetch_done', evt => {
            const d = JSON.parse(evt.data);
            fetched++;
            setProgress('national', fetched, totalDatasets);
            appendLog('national', `  ✓ ${d.name} — ${d.version || ''}`);
        });
        es.addEventListener('fetch_skipped', evt => {
            const d = JSON.parse(evt.data);
            fetched++;
            setProgress('national', fetched, totalDatasets);
            appendLog('national', `  ↩ ${d.name} — already up-to-date`);
        });
        es.addEventListener('fetch_error', evt => {
            const d = JSON.parse(evt.data);
            fetched++;
            setProgress('national', fetched, totalDatasets);
            appendLog('national', `  ✗ ${d.name} — ${d.error}`);
        });
        es.addEventListener('done', evt => {
            const d = JSON.parse(evt.data);
            const results = d.summary || [];
            const ok      = results.filter(r => !r.error && !r.skipped).length;
            const skipped = results.filter(r => r.skipped).length;
            const errors  = results.filter(r => r.error).length;
            appendLog('national', `\nDone. ${ok} new, ${skipped} up-to-date, ${errors} error(s).`);
            setBadge('national', errors > 0 ? 'Done (errors)' : 'Done', errors > 0 ? 'bg-warning text-dark' : 'bg-success');
            markSlotDone('national', errors === 0);
            setProgress('national', totalDatasets, totalDatasets);
            es.close();
            jobs.national.done = true;
            checkAllDone();
        });
        es.onerror = () => {
            appendLog('national', '[connection lost]');
            setBadge('national', 'Error', 'bg-danger');
            markSlotDone('national', false);
            es.close();
            jobs.national.done = true;
            checkAllDone();
        };
    }

    // ── Show results table after trust/ICB run completes ───────────────────
    function showResultsAfterRun(source, jobId, doneMsg) {
        if (source === 'trust') lastTrustJobId = jobId;
        if (source === 'icb')   lastICBJobId   = jobId;

        // show summary alert
        const failCount = doneMsg.failures || 0;
        scrapeSummary.classList.remove('d-none', 'alert-success', 'alert-warning');
        scrapeSummary.classList.add(failCount > 0 ? 'alert-warning' : 'alert-success');
        const orgLabel = source === 'icb' ? 'ICB' : 'trust';
        let html = `${cap(source)}: ${doneMsg.completed} ${orgLabel}(s) processed, ${doneMsg.downloads} file(s) downloaded.`;
        if (failCount > 0) {
            const names = (doneMsg.failed_names || []).map(n => `<li>${n}</li>`).join('');
            html += ` <strong>${failCount} had no results:</strong><ul class="mb-0 mt-1">${names}</ul>`;
        }
        scrapeSummary.innerHTML = (scrapeSummary.innerHTML || '') + (scrapeSummary.innerHTML ? '<br>' : '') + html;
        resultsSection.classList.remove('d-none');

        // fetch and merge results table
        fetch(`/scrape/results/${jobId}`)
            .then(r => r.json())
            .then(rows => renderResultsTable(rows))
            .catch(() => {});
    }

    let allResultsRows = [];

    function renderResultsTable(newRows) {
        // merge rows with existing (different sources may contribute different columns)
        newRows.forEach(r => {
            const idx = allResultsRows.findIndex(e => e.trust === r.trust);
            if (idx >= 0) {
                allResultsRows[idx] = Object.assign({}, allResultsRows[idx], r);
            } else {
                allResultsRows.push(r);
            }
        });
        const allKeys = Object.keys(TYPE_LABELS);
        const activeCols = allKeys.filter(k => allResultsRows.some(r => Array.isArray(r[k]) && r[k].length));

        if (resultsHeaderRow) {
            resultsHeaderRow.innerHTML = '<th>Organisation</th>' +
                activeCols.map(k => `<th>${TYPE_LABELS[k]}</th>`).join('');
        }
        if (resultsBody) {
            resultsBody.innerHTML = '';
            allResultsRows.forEach(row => {
                const tr = document.createElement('tr');
                let html = `<td>${row.trust}</td>`;
                activeCols.forEach(key => {
                    const val = row[key];
                    html += (val && val.length)
                        ? `<td class="text-success fw-semibold">${val.join('<br>')}</td>`
                        : `<td class="text-muted">—</td>`;
                });
                tr.innerHTML = html;
                resultsBody.appendChild(tr);
            });
        }
    }

    // ── CSV export ─────────────────────────────────────────────────────────
    exportCsvBtn && exportCsvBtn.addEventListener('click', () => {
        const jobId = lastTrustJobId || lastICBJobId;
        if (jobId) {
            window.location.href = `/scrape/export/${jobId}`;
        } else {
            // fall back to client-side CSV from in-memory results
            if (!allResultsRows.length) return;
            const allKeys = Object.keys(TYPE_LABELS);
            const activeCols = allKeys.filter(k => allResultsRows.some(r => Array.isArray(r[k]) && r[k].length));
            const header = ['Organisation', ...activeCols.map(k => TYPE_LABELS[k])];
            const lines = [header.map(h => `"${h}"`).join(',')];
            allResultsRows.forEach(row => {
                const cells = [row.trust, ...activeCols.map(k => {
                    const v = row[k];
                    return Array.isArray(v) ? v.join('; ') : (v || '—');
                })];
                lines.push(cells.map(c => `"${String(c).replace(/"/g, '""')}"`).join(','));
            });
            const blob = new Blob([lines.join('\r\n')], { type: 'text/csv' });
            const url  = URL.createObjectURL(blob);
            const a    = document.createElement('a');
            a.href = url;
            a.download = `scrape-results-${new Date().toISOString().slice(0, 10)}.csv`;
            a.click();
            URL.revokeObjectURL(url);
        }
    });

    // ── Cancel buttons ─────────────────────────────────────────────────────
    ['trust', 'icb'].forEach(source => {
        const btn = document.getElementById('cancel' + cap(source) + 'Btn');
        btn && btn.addEventListener('click', () => {
            const jobId = jobs[source].jobId;
            if (!jobId) return;
            fetch(`/scrape/cancel/${jobId}`, { method: 'DELETE' }).catch(() => {});
            appendLog(source, 'Cancellation requested…');
        });
    });

    // ── Start All ──────────────────────────────────────────────────────────
    startBtn.addEventListener('click', () => {
        activeSources = Array.from(document.querySelectorAll('.source-checkbox:checked')).map(cb => cb.value);
        if (!activeSources.length) {
            globalStatus.textContent = 'Select at least one source.';
            return;
        }

        // reset state
        activeSources.forEach(s => { jobs[s].done = false; jobs[s].jobId = null; });
        allResultsRows = [];
        lastTrustJobId = null;
        lastICBJobId = null;

        startBtn.disabled = true;
        globalStatus.textContent = `Starting ${activeSources.length} source(s)…`;
        progressSection.classList.remove('d-none');
        resultsSection.classList.add('d-none');
        if (scrapeSummary) { scrapeSummary.classList.add('d-none'); scrapeSummary.innerHTML = ''; }
        if (resultsBody) resultsBody.innerHTML = '';

        // show only active progress slots, hide others
        ['trust', 'icb', 'national'].forEach(s => {
            const slot = document.getElementById('progress' + cap(s));
            if (!slot) return;
            const active = activeSources.includes(s);
            slot.classList.toggle('d-none', !active);
            slot.classList.remove('slot-done', 'slot-error');
            if (active) {
                setBadge(s, 'Starting', 'bg-secondary');
                setProgress(s, 0, 0);
                const logEl = document.getElementById('log' + cap(s));
                if (logEl) logEl.textContent = '';
            }
        });

        const shared = getSharedSettings();

        // Fire trust job
        if (activeSources.includes('trust')) {
            const payload = {
                ...shared,
                source: 'trust',
                types: getSelectedTypes(TRUST_TYPES),
                date_filters: getDateFilters(TRUST_TYPES),
                trust_names: getSelectedOrgs('trustSelect'),
            };
            fetch('/scrape/start', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) })
                .then(r => r.json())
                .then(data => {
                    if (data.error) {
                        appendLog('trust', 'Error: ' + data.error);
                        setBadge('trust', 'Error', 'bg-danger');
                        jobs.trust.done = true; checkAllDone(); return;
                    }
                    jobs.trust.jobId = data.job_id;
                    jobs.trust.total = data.trust_count || 0;
                    setBadge('trust', 'Running', 'bg-primary');
                    appendLog('trust', `Job ${data.job_id} started (${data.trust_count} trusts)`);
                    openScrapeStream('trust', data.job_id, 'trust');
                })
                .catch(err => {
                    appendLog('trust', 'Request failed: ' + err);
                    setBadge('trust', 'Error', 'bg-danger');
                    jobs.trust.done = true; checkAllDone();
                });
        }

        // Fire ICB job
        if (activeSources.includes('icb')) {
            const payload = {
                ...shared,
                source: 'icb',
                types: getSelectedTypes(ICB_TYPES),
                date_filters: getDateFilters(ICB_TYPES),
                trust_names: getSelectedOrgs('icbSelect'),
            };
            fetch('/scrape/start', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) })
                .then(r => r.json())
                .then(data => {
                    if (data.error) {
                        appendLog('icb', 'Error: ' + data.error);
                        setBadge('icb', 'Error', 'bg-danger');
                        jobs.icb.done = true; checkAllDone(); return;
                    }
                    jobs.icb.jobId = data.job_id;
                    jobs.icb.total = data.trust_count || 0;
                    setBadge('icb', 'Running', 'bg-primary');
                    appendLog('icb', `Job ${data.job_id} started (${data.trust_count} ICBs)`);
                    openScrapeStream('icb', data.job_id, 'ICB');
                })
                .catch(err => {
                    appendLog('icb', 'Request failed: ' + err);
                    setBadge('icb', 'Error', 'bg-danger');
                    jobs.icb.done = true; checkAllDone();
                });
        }

        // Fire national job
        if (activeSources.includes('national')) {
            const selectedKeys = Array.from(document.querySelectorAll('.national-source-cb:checked')).map(cb => cb.value);
            if (!selectedKeys.length) {
                appendLog('national', 'No datasets selected.');
                setBadge('national', 'Skipped', 'bg-secondary');
                jobs.national.done = true; checkAllDone();
            } else {
                fetch('/national/fetch', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ source_keys: selectedKeys }) })
                    .then(r => r.json())
                    .then(data => {
                        if (data.error) {
                            appendLog('national', 'Error: ' + data.error);
                            setBadge('national', 'Error', 'bg-danger');
                            jobs.national.done = true; checkAllDone(); return;
                        }
                        jobs.national.jobId = data.job_id;
                        setBadge('national', 'Running', 'bg-info text-dark');
                        appendLog('national', `Job ${data.job_id} started (${selectedKeys.length} datasets)`);
                        openNationalStream(data.job_id, selectedKeys.length);
                    })
                    .catch(err => {
                        appendLog('national', 'Request failed: ' + err);
                        setBadge('national', 'Error', 'bg-danger');
                        jobs.national.done = true; checkAllDone();
                    });
            }
        }
    });

}());


// ---------------------------------------------------------------------------
// FailureLog — shows orgs with consecutive failures
// ---------------------------------------------------------------------------
(function FailureLog() {
    const tbody         = document.getElementById('failureLogBody');
    const clearAllBtn   = document.getElementById('clearAllFailuresBtn');
    const sectionToggle = document.getElementById('failureLogSection');

    if (!tbody) return;

    function loadFailureLog() {
        fetch('/scrape/failures')
            .then(r => r.json())
            .then(entries => {
                if (!entries.length) {
                    tbody.innerHTML = '<tr><td colspan="6" class="text-muted text-center py-3">No failures recorded.</td></tr>';
                    return;
                }
                tbody.innerHTML = '';
                entries.forEach(e => {
                    const tr = document.createElement('tr');
                    const sourceBadge = e.source === 'trust'
                        ? '<span class="badge bg-primary">Trust</span>'
                        : e.source === 'icb'
                            ? '<span class="badge bg-info text-dark">ICB</span>'
                            : '<span class="badge bg-secondary">?</span>';
                    const reasonLabel = e.reason === 'no_results' ? 'No documents found' : 'Scrape error';
                    tr.innerHTML = `
                        <td>${e.name}</td>
                        <td>${sourceBadge}</td>
                        <td>${e.failed_at || '—'}</td>
                        <td class="text-center"><span class="badge ${e.consecutive >= 3 ? 'bg-danger' : e.consecutive >= 2 ? 'bg-warning text-dark' : 'bg-secondary'}">${e.consecutive}</span></td>
                        <td>${reasonLabel}</td>
                        <td><button class="btn btn-sm btn-outline-danger btn-clear-failure" data-name="${encodeURIComponent(e.name)}">Clear</button></td>
                    `;
                    tbody.appendChild(tr);
                });

                tbody.querySelectorAll('.btn-clear-failure').forEach(btn => {
                    btn.addEventListener('click', () => {
                        const name = decodeURIComponent(btn.dataset.name);
                        fetch(`/scrape/failures/${encodeURIComponent(name)}`, { method: 'DELETE' })
                            .then(r => r.json())
                            .then(d => { if (d.success) loadFailureLog(); })
                            .catch(() => {});
                    });
                });
            })
            .catch(() => {
                tbody.innerHTML = '<tr><td colspan="6" class="text-danger text-center">Failed to load.</td></tr>';
            });
    }

    // Load when section is opened
    if (sectionToggle) {
        sectionToggle.addEventListener('show.bs.collapse', loadFailureLog);
    }
    // Also expose for post-run refresh
    window.loadFailureLog = loadFailureLog;

    clearAllBtn && clearAllBtn.addEventListener('click', () => {
        if (!confirm('Clear all failure records? This cannot be undone.')) return;
        fetch('/scrape/failures/clear-all', { method: 'DELETE' })
            .then(r => r.json())
            .then(d => { if (d.success) loadFailureLog(); })
            .catch(() => {});
    });

}());


// ---------------------------------------------------------------------------
// OrgEditor — add / edit org config entries
// ---------------------------------------------------------------------------
(function OrgEditor() {
    const editorSource       = document.getElementById('editorSource');
    const editorOrgSelect    = document.getElementById('editorOrgSelect');
    const editorNewBtn       = document.getElementById('editorNewBtn');
    const editorForm         = document.getElementById('editorForm');
    const editorName         = document.getElementById('editorName');
    const editorUrl          = document.getElementById('editorUrl');
    const editorStartUrls    = document.getElementById('editorStartUrls');
    const editorAllowedDomains = document.getElementById('editorAllowedDomains');
    const editorSaveBtn      = document.getElementById('editorSaveBtn');
    const editorCancelBtn    = document.getElementById('editorCancelBtn');
    const editorAlert        = document.getElementById('editorAlert');

    if (!editorSource) return;

    let isNewMode = false;

    function showAlert(msg, type) {
        editorAlert.className = `alert alert-${type} py-2`;
        editorAlert.textContent = msg;
        editorAlert.classList.remove('d-none');
    }

    function clearAlert() {
        editorAlert.classList.add('d-none');
        editorAlert.textContent = '';
    }

    function loadOrgDropdown() {
        const source = editorSource.value;
        const endpoint = source === 'trust' ? '/scrape/trusts' : '/scrape/icbs';
        editorOrgSelect.innerHTML = '<option value="">— Select to edit an existing org —</option>';
        fetch(endpoint)
            .then(r => r.json())
            .then(items => {
                items.forEach(t => {
                    const opt = document.createElement('option');
                    opt.value = t.name;
                    opt.textContent = t.name;
                    editorOrgSelect.appendChild(opt);
                });
            })
            .catch(() => {});
    }

    editorSource.addEventListener('change', loadOrgDropdown);

    editorOrgSelect.addEventListener('change', () => {
        const name = editorOrgSelect.value;
        if (!name) { editorForm.classList.add('d-none'); return; }
        const source = editorSource.value;
        clearAlert();
        fetch(`/config/org?name=${encodeURIComponent(name)}&source=${source}`)
            .then(r => r.json())
            .then(data => {
                if (data.error) { showAlert(data.error, 'danger'); return; }
                isNewMode = false;
                editorName.value          = data.name || '';
                editorUrl.value           = data.url  || '';
                editorStartUrls.value     = (data.start_urls || []).join('\n');
                editorAllowedDomains.value= (data.allowed_domains || []).join(', ');
                editorName.disabled       = true;
                editorForm.classList.remove('d-none');
            })
            .catch(() => showAlert('Failed to load org data.', 'danger'));
    });

    editorNewBtn.addEventListener('click', () => {
        isNewMode = true;
        editorOrgSelect.value     = '';
        editorName.value          = '';
        editorUrl.value           = '';
        editorStartUrls.value     = '';
        editorAllowedDomains.value= '';
        editorName.disabled       = false;
        clearAlert();
        editorForm.classList.remove('d-none');
        editorName.focus();
    });

    editorCancelBtn.addEventListener('click', () => {
        editorForm.classList.add('d-none');
        editorOrgSelect.value = '';
        clearAlert();
    });

    editorSaveBtn.addEventListener('click', () => {
        clearAlert();
        const source     = editorSource.value;
        const name       = editorName.value.trim();
        const url        = editorUrl.value.trim();
        const startUrls  = editorStartUrls.value.split('\n').map(s => s.trim()).filter(Boolean);
        const domains    = editorAllowedDomains.value.split(',').map(s => s.trim()).filter(Boolean);

        if (!name) { showAlert('Organisation name is required.', 'warning'); return; }
        if (!url)  { showAlert('Website URL is required.', 'warning'); return; }

        const payload = { name, source, url, start_urls: startUrls, allowed_domains: domains.length ? domains : undefined };
        const method  = isNewMode ? 'POST' : 'PUT';

        editorSaveBtn.disabled = true;
        fetch('/config/org', {
            method,
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        })
        .then(r => r.json())
        .then(data => {
            if (data.error) { showAlert(data.error, 'danger'); return; }
            showAlert(`Saved "${name}" successfully. Changes take effect on the next scrape.`, 'success');
            editorOrgSelect.value = '';
            editorForm.classList.add('d-none');
            loadOrgDropdown();
        })
        .catch(err => showAlert('Save failed: ' + err.message, 'danger'))
        .finally(() => { editorSaveBtn.disabled = false; });
    });

    // Initial load
    loadOrgDropdown();

}());
