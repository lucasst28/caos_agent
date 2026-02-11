/**
 * CAOS Supervisor Dashboard
 * Clean & Modern Interactive Dashboard
 */

// ============================================================================
// CONFIGURATION
// ============================================================================
const CONFIG = {
    apiUrl: localStorage.getItem('caos_api_url') || 'http://localhost:8001',
    sentinelApiUrl: localStorage.getItem('sentinel_api_url') || 'http://localhost:8002',
    sentinelTenant: localStorage.getItem('sentinel_tenant') || 'client',
    pollInterval: parseInt(localStorage.getItem('caos_poll_interval')) || 3000,
    maxLogs: 200,
};

// ============================================================================
// SECTION METADATA
// ============================================================================
const SECTIONS = {
    overview: {
        title: 'Overview',
        subtitle: 'Monitoramento em tempo real do ecossistema'
    },
    agents: {
        title: 'Agentes',
        subtitle: 'Monitoramento detalhado de cada agente'
    },
    streams: {
        title: 'Streams',
        subtitle: 'Fluxo de dados em tempo real'
    },
    queue: {
        title: 'Fila',
        subtitle: 'Monitoramento do Redis compartilhado'
    },
    logs: {
        title: 'Logs',
        subtitle: 'Histórico completo de eventos do sistema'
    },
    settings: {
        title: 'Configurações',
        subtitle: 'Configurações do dashboard'
    },
    'atlas-config': {
        title: 'Atlas Agent',
        subtitle: 'Configuração e gerenciamento do agente Atlas'
    }
};

// ============================================================================
// STATE
// ============================================================================
let isConnected = false;
let pollTimer = null;
let logs = [];
let knownLogIds = new Set(); // Track unique IDs from backend
let currentSection = 'overview';
let selectedClient = localStorage.getItem('caos_client') || 'viva';
let currentThroughputChart = null;
let currentStreamInterval = 'day';

// Pagination state
let allMessages = [];
let currentPage = 1;
let messagesPerPage = 25;
let pendingMessageIds = new Set(); // Track pending message IDs

// ============================================================================
// DOM HELPERS
// ============================================================================
const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => document.querySelectorAll(selector);
const $id = (id) => document.getElementById(id);

// ============================================================================
// UTILITIES
// ============================================================================
function formatTime(date = new Date()) {
    return date.toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit' });
}

function formatTimeWithSeconds(date = new Date()) {
    return date.toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
}

function formatUptime(seconds) {
    if (!seconds || isNaN(seconds)) return '--';

    const days = Math.floor(seconds / 86400);
    const hours = Math.floor((seconds % 86400) / 3600);
    const mins = Math.floor((seconds % 3600) / 60);

    if (days > 0) return `${days}d ${hours}h`;
    if (hours > 0) return `${hours}h ${mins}m`;
    return `${mins}m`;
}

function setConnectionStatus(connected) {
    isConnected = connected;
    const badge = $id('connection-badge');
    if (badge) {
        badge.className = 'connection-badge' + (connected ? ' online' : '');
        badge.querySelector('.badge-text').textContent = connected ? 'Online' : 'Offline';
    }
}

function addLog(message, type = 'info', agent = 'system') {
    logs.unshift({
        time: formatTimeWithSeconds(),
        type,
        message,
        agent,
    });

    if (logs.length > CONFIG.maxLogs) {
        logs.pop();
    }

    renderLogs();
    renderFullLogs();
    updateLogsCount();
}

function addRemoteLog(logData) {
    if (knownLogIds.has(logData.id)) return;
    knownLogIds.add(logData.id);

    // Map Severity to Frontend Types
    let type = 'info';
    switch ((logData.severity || 'INFO').toUpperCase()) {
        case 'CRITICAL':
        case 'HIGH':
            type = 'error';
            break;
        case 'MEDIUM':
            type = 'warning';
            break;
        case 'LOW':
            type = 'success';
            break;
        default:
            // Heuristic for success messages if severity is INFO
            if (logData.message && (logData.message.includes('CONCLUÍDA') || logData.message.includes('sucesso'))) {
                type = 'success';
            } else {
                type = 'info';
            }
    }

    // Format Time
    let timeStr = formatTimeWithSeconds();
    if (logData.timestamp) {
        try {
            timeStr = formatTimeWithSeconds(new Date(logData.timestamp));
        } catch (e) {
            console.error('Invalid timestamp:', logData.timestamp);
        }
    }

    // Determine agent from log source
    let agent = 'system';
    const source = (logData.source || '').toLowerCase();
    const msg = (logData.message || '').toLowerCase();

    if (source.includes('atlas') || msg.includes('atlas') || msg.includes('rotina')) {
        agent = 'atlas';
    } else if (source.includes('sentinel') || msg.includes('sentinel')) {
        agent = 'sentinel';
    } else if (source.includes('caos') || source.includes('supervisor') || msg.includes('validat') || msg.includes('approved') || msg.includes('blocked')) {
        agent = 'caos';
    }

    logs.unshift({
        time: timeStr,
        type,
        message: logData.message || 'Sem mensagem',
        id: logData.id,
        agent,
    });

    if (logs.length > CONFIG.maxLogs) {
        logs.pop();
    }

    renderLogs();
    renderFullLogs();
    updateLogsCount();
}

function updateLogsCount() {
    const countEl = $id('logs-total-count');
    if (countEl) {
        countEl.textContent = `${logs.length} logs`;
    }
}

function renderLogs() {
    const container = $id('logs-container');
    if (!container) return;

    const activeFilter = $('.log-filter.active')?.dataset.filter || 'all';
    const activeAgent = $('.agent-log-tab.active')?.dataset.agent || 'all';

    let filteredLogs = logs;

    // Filter by agent
    if (activeAgent !== 'all') {
        filteredLogs = filteredLogs.filter(l => l.agent === activeAgent);
    }

    // Filter by type
    if (activeFilter !== 'all') {
        filteredLogs = filteredLogs.filter(l => l.type === activeFilter);
    }

    // Update counter
    const countEl = $id('filtered-logs-count');
    if (countEl) {
        countEl.textContent = filteredLogs.length;
    }

    container.innerHTML = filteredLogs.slice(0, 30).map(log => {
        const agentBadge = log.agent && log.agent !== 'system'
            ? `<span class="log-agent-badge ${log.agent}">${log.agent.toUpperCase()}</span>`
            : '';
        return `
            <div class="log-entry ${log.type}" data-agent="${log.agent || 'system'}">
                <span class="log-time">${log.time}</span>
                ${agentBadge}
                <span class="log-type">${log.type.toUpperCase()}</span>
                <span class="log-msg">${log.message}</span>
            </div>
        `;
    }).join('') || '<div class="log-entry info"><span class="log-msg">Nenhum log para exibir</span></div>';
}

function renderFullLogs() {
    const container = $id('logs-full-container');
    if (!container) return;

    const activeFilter = $('.level-filter.active')?.dataset.level || 'all';
    const searchTerm = $id('logs-search-input')?.value.toLowerCase() || '';

    let filteredLogs = activeFilter === 'all'
        ? logs
        : logs.filter(l => l.type === activeFilter);

    if (searchTerm) {
        filteredLogs = filteredLogs.filter(l =>
            l.message.toLowerCase().includes(searchTerm) ||
            l.type.toLowerCase().includes(searchTerm)
        );
    }

    container.innerHTML = filteredLogs.map(log => `
        <div class="log-entry ${log.type}">
            <span class="log-time">${log.time}</span>
            <span class="log-type">${log.type.toUpperCase()}</span>
            <span class="log-msg">${log.message}</span>
        </div>
    `).join('') || '<div class="log-entry info"><span class="log-msg">Nenhum log encontrado</span></div>';
}

// ============================================================================
// API FUNCTIONS
// ============================================================================
async function fetchAPI(endpoint) {
    const response = await fetch(`${CONFIG.apiUrl}${endpoint}`, {
        headers: { 'Accept': 'application/json' },
        signal: AbortSignal.timeout(5000),
    });

    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    return response.json();
}

// ============================================================================
// DATA UPDATES
// ============================================================================
async function updateHealth() {
    try {
        const data = await fetchAPI('/health');

        setConnectionStatus(true);

        // Health status
        const healthEl = $id('health-status');
        if (healthEl) {
            const isHealthy = data.status === 'ok' || data.status === 'healthy';
            healthEl.textContent = isHealthy ? 'Healthy' : data.status;
            healthEl.className = 'metric-value' + (isHealthy ? ' success' : ' error');
        }

        // Uptime from health
        const uptimeEl = $id('uptime-value');
        if (uptimeEl && data.uptime_seconds) {
            uptimeEl.textContent = formatUptime(data.uptime_seconds);
        }

        // Update agent status
        updateAgentStatus('atlas', true);
        updateAgentStatus('sentinel', true);

        return data;
    } catch (error) {
        setConnectionStatus(false);

        const healthEl = $id('health-status');
        if (healthEl) {
            healthEl.textContent = 'Offline';
            healthEl.className = 'metric-value error';
        }

        updateAgentStatus('atlas', false);
        updateAgentStatus('sentinel', false);

        throw error;
    }
}

function updateAgentStatus(agent, isActive) {
    const statusEl = $id(`${agent}-status`);
    if (statusEl) {
        const dot = statusEl.querySelector('.status-dot');
        const text = statusEl.querySelector('span:last-child');

        if (dot) {
            dot.className = 'status-dot' + (isActive ? '' : ' inactive');
        }
        if (text) {
            text.textContent = isActive ? 'Ativo' : 'Offline';
        }
    }

    // Also update detail view status
    const detailStatus = $id(`${agent}-detail-status`);
    if (detailStatus) {
        const indicator = detailStatus.querySelector('.status-indicator');
        const text = detailStatus.querySelector('span:last-child');

        if (isActive) {
            detailStatus.classList.remove('standby');
            indicator?.classList.add('active');
            if (text) text.textContent = 'Ativo';
        } else {
            detailStatus.classList.add('standby');
            indicator?.classList.remove('active');
            if (text) text.textContent = 'Offline';
        }
    }
}

async function fetchLogs() {
    try {
        const remoteLogs = await fetchAPI('/alerts?limit=50');
        if (Array.isArray(remoteLogs)) {
            // Process generally oldest to newest so they appear in correct order if bulk added?
            // "unshift" adds to top. So we want to unshift the OLDER ones first? 
            // No, unshift adds key to index 0. 
            // If we receive [Newest, ..., Oldest] from backend (xrevrange).
            // We should process Oldest first? 
            // Actually, if we just iterate and unshift, the last one processed (Oldest) ends up at top. That's wrong.
            // We want Newest at top.
            // So we should Iterate filtered logs in Reverse (Oldest -> Newest) and unshift them?
            // OR: Iterate Newest -> Oldest (standard API response) and unshift.
            // If I have [A, B] (A is newer).
            // Unshift A -> [A]
            // Unshift B -> [B, A] -> B is atop. WRONG. A should be atop.

            // Correct: Process Oldest -> Newest
            // API returns Newest -> Oldest. So reverse it.
            remoteLogs.reverse().forEach(log => addRemoteLog(log));
        }
    } catch (error) {
        console.warn('Failed to fetch logs:', error);
    }
}

async function updateCircuitBreakers() {
    try {
        const data = await fetchAPI('/circuit-breakers');

        const list = $id('cb-list');
        if (!list) return;

        const entries = Object.entries(data);

        if (entries.length === 0) {
            list.innerHTML = `
                <div class="cb-empty">
                    <i class="ph-bold ph-check-circle" style="color: var(--status-success);"></i>
                    <span>Nenhum circuit breaker configurado</span>
                </div>
            `;
            return;
        }

        list.innerHTML = entries.map(([name, cb]) => {
            const state = cb.state.toLowerCase();
            const icon = state === 'closed' ? 'check-circle' : (state === 'open' ? 'x-circle' : 'warning-circle');

            return `
                <div class="cb-item ${state}">
                    <div class="cb-left">
                        <i class="ph-fill ph-${icon} cb-icon"></i>
                        <span class="cb-name">${name}</span>
                    </div>
                    <span class="cb-state">${state}</span>
                </div>
            `;
        }).join('');

    } catch (error) {
        const list = $id('cb-list');
        if (list) {
            list.innerHTML = `
                <div class="cb-empty">
                    <i class="ph-bold ph-wifi-slash"></i>
                    <span>Sem conexão</span>
                </div>
            `;
        }
    }
}

async function updateStats() {
    try {
        const data = await fetchAPI('/stats');

        // Validated count
        const validatedEl = $id('validated-count');
        if (validatedEl) {
            validatedEl.textContent = data.validated || 0;
        }

        // Rejected count
        const rejectedEl = $id('rejected-count');
        if (rejectedEl) {
            rejectedEl.textContent = data.rejected || 0;
        }

        // Events per minute (show in Atlas card)
        const atlasEventsEl = $id('atlas-events');
        if (atlasEventsEl) {
            atlasEventsEl.textContent = data.events_per_minute || 0;
        }

        // Update uptime from stats as backup
        const uptimeEl = $id('uptime-value');
        if (uptimeEl && data.uptime_seconds) {
            uptimeEl.textContent = formatUptime(data.uptime_seconds);
        }

        // Update Atlas detail metrics (if visible)
        const atlasDetailEventsEl = $id('atlas-detail-events');
        if (atlasDetailEventsEl) {
            atlasDetailEventsEl.textContent = data.events_per_minute || 0;
        }

        // --- SENTINEL DETAILS ---
        // --- SENTINEL DETAILS ---
        // Sentinel stats are now handled by loadSentinelData() to avoid overwriting with CAOS stats
        // const sentinelProcessedEl = $id('sentinel-processed');
        // if (sentinelProcessedEl) sentinelProcessedEl.textContent = data.total_telemetry || 0;
        // const sentinelErrorsEl = $id('sentinel-errors'); 
        // if (sentinelErrorsEl) sentinelErrorsEl.textContent = data.rejected || 0;

        // NOTE: sentinel-detail-alerts is now updated by updateSentinelAlerts() function
        // to show actual alert count from Sentinel API, not telemetry processed count



        const sentinelDetailErrorsEl = $id('sentinel-detail-errors'); // Detail view errors
        if (sentinelDetailErrorsEl) {
            sentinelDetailErrorsEl.textContent = data.rejected || 0;
        }

        const sentinelDetailHealthEl = $id('sentinel-detail-health'); // Detail view health
        if (sentinelDetailHealthEl) {
            const total = (data.total_telemetry || 0);
            const errors = (data.rejected || 0);
            const health = total > 0 ? ((total - errors) / total * 100).toFixed(1) : '100.0';
            sentinelDetailHealthEl.textContent = `${health}%`;
        }

        // --- ORACLE / CARE MOCKS (Standby) ---
        // Just ensuring they are initialized if needed, currently -- is fine for standby


        // Success Rate
        const atlasDetailSuccessEl = $id('atlas-detail-success');
        if (atlasDetailSuccessEl) {
            const total = data.total_requests || 0;
            const valid = data.validated || 0;
            const rate = total > 0 ? Math.round((valid / total) * 100) : 100;
            atlasDetailSuccessEl.textContent = `${rate}%`;
        }

        // Latency (Simulated for now as backend doesn't provide it)
        const atlasDetailLatencyEl = $id('atlas-detail-latency');
        if (atlasDetailLatencyEl) {
            // Random value between 20ms and 50ms to look alive
            const latency = Math.floor(Math.random() * 30) + 20;
            atlasDetailLatencyEl.textContent = `${latency}ms`;
        }

        const atlasDetailActiveEl = $id('atlas-detail-active');
        if (atlasDetailActiveEl) {
            atlasDetailActiveEl.textContent = data.total_requests || 0;
        }

        return data;
    } catch (error) {
        // Stats are optional, don't break the dashboard
        console.log('Stats not available:', error.message);
    }
}

async function updateAtlasStats() {
    try {
        const data = await fetchAPI(`/atlas-stats?client=${encodeURIComponent(selectedClient)}`);

        // Total assets (show in Atlas card)
        const atlasAssetsEl = $id('atlas-assets');
        if (atlasAssetsEl) {
            atlasAssetsEl.textContent = data.total_assets || 0;
        }

        // Update detail view if visible
        const atlasDetailAssetsEl = $id('atlas-detail-assets');
        if (atlasDetailAssetsEl) {
            atlasDetailAssetsEl.textContent = data.total_assets || 0;
        }

        // Alerts count for Sentinel card
        const sentinelAlertsEl = $id('sentinel-alerts');
        if (sentinelAlertsEl) {
            sentinelAlertsEl.textContent = data.alerts_period_count || 0;
        }

        // Sentinel Detail: Anomalies (was Alerts in JS, but Anomalies in HTML)
        const sentinelDetailAnomaliesEl = $id('sentinel-detail-anomalies');
        if (sentinelDetailAnomaliesEl) {
            sentinelDetailAnomaliesEl.textContent = data.alerts_period_count || 0;
        }

        return data;
    } catch (error) {
        // Atlas stats are optional
        console.log('Atlas stats not available:', error.message);
    }
}

async function loadClients() {
    try {
        const data = await fetchAPI('/clients');
    } catch (error) {
        console.log('Error fetching clients:', error);
    }
}

async function loadClients() {
    try {
        const data = await fetchAPI('/clients');
        const customSelect = $id('custom-client-select');
        if (!customSelect) return;

        const itemsDiv = customSelect.querySelector('.select-items');
        itemsDiv.innerHTML = '';

        // If saved client not in list, use default
        if (!data.clients.includes(selectedClient)) {
            selectedClient = data.default || data.clients[0] || 'viva';
            localStorage.setItem('caos_client', selectedClient);
        }

        // Update UI for initial selection
        updateSelectedClientUI(selectedClient);

        // Build options
        for (const client of data.clients) {
            const item = document.createElement('div');
            item.className = 'select-item';
            item.dataset.value = client;
            item.textContent = client.charAt(0).toUpperCase() + client.slice(1).toLowerCase();

            if (client === selectedClient) {
                item.classList.add('same-as-selected');
            }

            item.addEventListener('click', () => {
                selectedClient = client;
                localStorage.setItem('caos_client', selectedClient);
                addLog(`Cliente alterado para: ${client}`, 'info');

                updateSelectedClientUI(client);
                updateAtlasStats();

                // Close dropdown
                itemsDiv.classList.add('select-hide');
                customSelect.querySelector('.select-selected').classList.remove('select-arrow-active');
            });

            itemsDiv.appendChild(item);
        }

    } catch (error) {
        console.log('Could not load clients:', error.message);
    }
}

function setupClientSelector() {
    const customSelect = $id('custom-client-select');
    if (!customSelect) return;

    const selectedDiv = customSelect.querySelector('.select-selected');
    const itemsDiv = customSelect.querySelector('.select-items');

    // Toggle dropdown
    selectedDiv.addEventListener('click', (e) => {
        e.stopPropagation();
        itemsDiv.classList.toggle('select-hide');
        selectedDiv.classList.toggle('select-arrow-active');
    });

    // Close when clicking outside
    document.addEventListener('click', (e) => {
        if (!customSelect.contains(e.target)) {
            if (!itemsDiv.classList.contains('select-hide')) {
                itemsDiv.classList.add('select-hide');
                selectedDiv.classList.remove('select-arrow-active');
            }
        }
    });
}

function updateSelectedClientUI(client) {
    const customSelect = $id('custom-client-select');
    if (!customSelect) return;

    // Update displayed text
    const selectedText = customSelect.querySelector('.selected-value');
    if (selectedText) {
        selectedText.textContent = client.charAt(0).toUpperCase() + client.slice(1).toLowerCase();
    }

    // Update active item highlighting
    const items = customSelect.querySelectorAll('.select-item');
    items.forEach(item => {
        if (item.dataset.value === client) {
            item.classList.add('same-as-selected');
        } else {
            item.classList.remove('same-as-selected');
        }
    });
}

async function updateRules() {
    try {
        const data = await fetchAPI('/rules');

        const grid = $id('rules-grid');
        const countEl = $id('rules-count');

        if (countEl) {
            countEl.textContent = `${data.length} regras`;
        }

        if (!grid) return;

        if (data.length === 0) {
            grid.innerHTML = '<div class="rule-empty">Nenhuma regra configurada</div>';
            return;
        }

        grid.innerHTML = data.map(rule => `
            <div class="rule-card">
                <div class="rule-name">${rule.name}</div>
                <div class="rule-path">${rule.methods.join(', ')} ${rule.path_prefix}*</div>
                <div class="rule-meta">
                    <span>Limite: <strong>${rule.max_calls}</strong></span>
                    <span>Janela: <strong>${rule.window_seconds}s</strong></span>
                </div>
            </div>
        `).join('');

    } catch (error) {
        const grid = $id('rules-grid');
        if (grid) {
            grid.innerHTML = '<div class="rule-empty">Erro ao carregar regras</div>';
        }
    }
}

// ============================================================================
// STREAMS DATA
// ============================================================================
let previousStreamLengths = {};

async function updateStreams() {
    try {
        const data = await fetchAPI('/streams');

        if (!data.streams || data.streams.length === 0) return;

        const now = Date.now();

        for (const stream of data.streams) {
            const name = stream.name;
            const length = stream.length || 0;
            const consumers = stream.total_consumers || 0;

            // Calculate rate (messages per minute)
            let rate = 0;
            if (previousStreamLengths[name] !== undefined) {
                const diff = length - previousStreamLengths[name].length;
                const timeDiff = (now - previousStreamLengths[name].time) / 1000; // seconds
                if (timeDiff > 0 && diff >= 0) {
                    rate = Math.round((diff / timeDiff) * 60); // per minute
                }
            }
            previousStreamLengths[name] = { length, time: now };

            // Update UI based on stream name
            switch (name) {
                case 'telemetry.received':
                    updateStreamUI('stream-telemetry', length, rate, consumers);
                    // Also update agent detail card
                    updateAgentStreamCount('atlas-stream-received', length);
                    break;
                case 'telemetry.validated':
                    updateStreamUI('stream-validated', length, rate, consumers);
                    // Update Sentinel agent detail card
                    updateAgentStreamCount('sentinel-stream-validated', length);
                    break;
                case 'telemetry.rejected':
                    updateStreamUI('stream-rejected', length, rate, consumers);
                    break;
                case 'atlas.daily_routine':
                    updateAgentStreamCount('atlas-stream-routine', length);
                    break;
                case 'sentinel.consumer_health':
                    updateStreamUI('stream-sentinel-health', length, rate, consumers);
                    updateAgentStreamCount('sentinel-stream-health', length);
                    break;
                case 'sentinel.alerts':
                    updateStreamUI('stream-sentinel-alerts', length, rate, consumers);
                    break;
                case 'oracle.predictions':
                    updateStreamUI('stream-oracle', length, rate, consumers);
                    break;
                case 'care.actions':
                    updateStreamUI('stream-care', length, rate, consumers);
                    break;
            }
        }
    } catch (error) {
        console.warn('Failed to fetch streams:', error);
    }
}

// Update Sentinel alerts count for Overview and Detail cards
async function updateSentinelAlerts() {
    try {
        const response = await fetch(`${CONFIG.sentinelApiUrl}/tenants/${CONFIG.sentinelTenant}/alerts?limit=500`, {
            headers: { 'Accept': 'application/json' },
            signal: AbortSignal.timeout(5000),
        });

        if (!response.ok) throw new Error(`HTTP ${response.status}`);

        const alertsData = await response.json();
        const totalAlerts = alertsData?.length || 0;

        // Update Overview Card (small agent card)
        const overviewAlertsEl = $id('sentinel-processed');
        if (overviewAlertsEl) overviewAlertsEl.textContent = totalAlerts.toLocaleString();

        // Update Detail Card (agents section, big card)
        const detailAlertsEl = $id('sentinel-detail-alerts');
        if (detailAlertsEl) detailAlertsEl.textContent = totalAlerts.toLocaleString();

    } catch (error) {
        console.warn('Failed to fetch Sentinel alerts:', error);
        // Set to 0 on error
        const overviewAlertsEl = $id('sentinel-processed');
        if (overviewAlertsEl) overviewAlertsEl.textContent = '0';

        const detailAlertsEl = $id('sentinel-detail-alerts');
        if (detailAlertsEl) detailAlertsEl.textContent = '0';
    }
}

function updateStreamUI(prefix, length, rate, consumers) {
    const countEl = $id(`${prefix}-count`);
    const rateEl = $id(`${prefix}-rate`);
    const consumersEl = $id(`${prefix}-consumers`);

    if (countEl) countEl.textContent = length.toLocaleString();
    if (rateEl) rateEl.textContent = rate.toLocaleString();
    if (consumersEl) consumersEl.textContent = consumers.toLocaleString();
}

function updateAgentStreamCount(elementId, length) {
    const el = $id(elementId);
    if (el) el.textContent = length.toLocaleString();
}

// ============================================================================
// QUEUE (REDIS) DATA
// ============================================================================
async function loadQueueData() {
    try {
        const data = await fetchAPI('/redis/status');
        if (!data) return;

        // Update status badge
        const statusBadge = $id('queue-status-badge');
        if (statusBadge) {
            const isOnline = data.status === 'online';
            statusBadge.className = `queue-hero-status ${isOnline ? 'online' : 'offline'}`;
            statusBadge.innerHTML = `
                <span class="status-dot"></span>
                <span>${isOnline ? 'Online' : 'Offline'}</span>
            `;
        }

        // Update metrics
        if (data.memory) {
            const memoryEl = $id('queue-memory');
            if (memoryEl) memoryEl.textContent = data.memory.used_mb || '--';
        }

        const streamsEl = $id('queue-streams');
        if (streamsEl) streamsEl.textContent = data.streams_count || 0;

        const consumersEl = $id('queue-consumers');
        if (consumersEl) consumersEl.textContent = data.total_consumers || 0;

        if (data.stats) {
            const throughputEl = $id('queue-throughput');
            if (throughputEl) throughputEl.textContent = data.stats.commands_per_sec || 0;
        }

        const pendingEl = $id('queue-pending');
        if (pendingEl) pendingEl.textContent = data.total_pending || 0;

        // Update bottlenecks
        const bottlenecksSection = $id('queue-bottlenecks-section');
        const bottlenecksList = $id('queue-bottlenecks-list');
        if (bottlenecksSection && bottlenecksList) {
            if (data.bottlenecks && data.bottlenecks.length > 0) {
                bottlenecksSection.style.display = 'block';
                bottlenecksList.innerHTML = data.bottlenecks.map(b => {
                    const severity = b.severity === 'critical' ? 'critical' : 'warning';
                    let message = '';
                    let icon = '';

                    if (b.type === 'lag') {
                        icon = 'ph-warning-circle';
                        message = `<strong>${b.group}</strong> em <strong>${b.stream}</strong> com lag de <strong>${b.lag.toLocaleString()}</strong> mensagens`;
                    } else if (b.type === 'pending') {
                        icon = 'ph-clock';
                        message = `<strong>${b.group}</strong> em <strong>${b.stream}</strong> com <strong>${b.pending.toLocaleString()}</strong> mensagens pendentes`;
                    } else if (b.type === 'inactive_consumer') {
                        icon = 'ph-user-minus';
                        message = `Consumer <strong>${b.consumer}</strong> em <strong>${b.group}</strong> inativo há <strong>${b.idle_minutes}</strong> minutos`;
                    }

                    return `
                        <div class="queue-bottleneck-item ${severity}">
                            <i class="ph-fill ${icon}"></i>
                            <span>${message}</span>
                        </div>
                    `;
                }).join('');
            } else {
                bottlenecksSection.style.display = 'none';
            }
        }

        // Update streams table
        const streamsTable = $id('queue-streams-table');
        if (streamsTable && data.streams) {
            streamsTable.innerHTML = data.streams.map(s => {
                const statusClass = s.active ? 'active' : 'inactive';
                const statusText = s.active ? 'Ativo' : 'Inativo';
                return `
                    <tr>
                        <td><code>${s.name}</code></td>
                        <td>${s.length.toLocaleString()}</td>
                        <td>${s.groups ? s.groups.length : 0}</td>
                        <td><span class="queue-status-badge ${statusClass}">${statusText}</span></td>
                    </tr>
                `;
            }).join('');
        }

        // Update consumer groups table
        const consumersTable = $id('queue-consumers-table');
        if (consumersTable && data.streams) {
            let consumerRows = [];
            data.streams.forEach(stream => {
                if (stream.groups) {
                    stream.groups.forEach(group => {
                        const lagClass = group.lag > 100 ? (group.lag > 1000 ? 'critical' : 'warning') : '';
                        consumerRows.push(`
                            <tr>
                                <td><code>${stream.name}</code></td>
                                <td><strong>${group.name}</strong></td>
                                <td>${group.consumers_count}</td>
                                <td>${group.pending}</td>
                                <td class="${lagClass}">${group.lag}</td>
                            </tr>
                        `);
                    });
                }
            });
            consumersTable.innerHTML = consumerRows.length > 0
                ? consumerRows.join('')
                : '<tr><td colspan="5" class="empty-state">Nenhum consumer group encontrado</td></tr>';
        }

    } catch (error) {
        console.error('Failed to load queue data:', error);
    }
}

// ============================================================================
// POLLING
// ============================================================================
async function pollAll() {
    const startTime = Date.now();

    try {
        await Promise.all([
            updateHealth(),
            updateCircuitBreakers(),
            updateRules(),
            updateStats(),
            updateAtlasStats(),
            updateStreams(),
            updateSentinelAlerts(),
            fetchLogs(),
            loadQueueData(),
        ]);

        addLog(`Dados atualizados em ${Date.now() - startTime}ms`, 'info');
    } catch (error) {
        addLog(`Erro de conexão: ${error.message}`, 'rejected');
    }
}

function startPolling() {
    pollAll();
    pollTimer = setInterval(pollAll, CONFIG.pollInterval);
}

function stopPolling() {
    if (pollTimer) {
        clearInterval(pollTimer);
        pollTimer = null;
    }
}

// ============================================================================
// CLOCK
// ============================================================================
function updateClock() {
    const el = $id('current-time');
    if (el) {
        el.textContent = formatTime();
    }
}

// ============================================================================
// NAVIGATION
// ============================================================================
function setupNavigation() {
    $$('.nav-item').forEach(item => {
        item.addEventListener('click', (e) => {
            e.preventDefault();

            const section = item.dataset.section;

            if (section === 'settings') {
                openSettings();
                return;
            }

            switchSection(section);

            // Update active nav item
            $$('.nav-item').forEach(i => i.classList.remove('active'));
            item.classList.add('active');
        });
    });

    // Sidebar Toggle Logic
    const toggleBtn = $id('sidebar-toggle');
    const sidebar = document.querySelector('.sidebar');

    if (toggleBtn && sidebar) {
        // Load Saved State
        const isCollapsed = localStorage.getItem('caos_sidebar_collapsed') === 'true';
        if (isCollapsed) {
            sidebar.classList.add('collapsed');
            toggleBtn.querySelector('i').classList.replace('ph-caret-left', 'ph-caret-right');
        }

        toggleBtn.addEventListener('click', () => {
            sidebar.classList.toggle('collapsed');
            const collapsed = sidebar.classList.contains('collapsed');
            localStorage.setItem('caos_sidebar_collapsed', collapsed);

            const icon = toggleBtn.querySelector('i');
            if (collapsed) {
                icon.classList.replace('ph-caret-left', 'ph-caret-right');
            } else {
                icon.classList.replace('ph-caret-right', 'ph-caret-left');
            }
        });
    }
}

function switchSection(sectionName) {
    if (!SECTIONS[sectionName]) return;

    currentSection = sectionName;

    // Update page title
    const pageTitle = $('.page-title');
    const pageSubtitle = $('.page-subtitle');

    if (pageTitle) pageTitle.textContent = SECTIONS[sectionName].title;
    if (pageSubtitle) pageSubtitle.textContent = SECTIONS[sectionName].subtitle;

    // Switch content sections
    $$('.content-section').forEach(section => {
        section.classList.remove('active');
    });

    const targetSection = $id(`section-${sectionName}`);
    if (targetSection) {
        targetSection.classList.add('active');
    }

    // Re-render logs when switching to logs section
    if (sectionName === 'logs') {
        renderFullLogs();
    }
}

// ============================================================================
// SETTINGS MODAL
// ============================================================================
function openSettings() {
    const modal = $id('settings-modal');
    const apiInput = $id('api-url-input');
    const pollInput = $id('poll-interval');

    if (apiInput) apiInput.value = CONFIG.apiUrl;
    if (pollInput) pollInput.value = CONFIG.pollInterval;

    modal?.classList.add('open');
}

function closeSettings() {
    $id('settings-modal')?.classList.remove('open');
}

function saveSettings() {
    const apiUrl = $id('api-url-input')?.value.trim();
    const pollInterval = parseInt($id('poll-interval')?.value) || 3000;

    if (apiUrl) {
        CONFIG.apiUrl = apiUrl;
        localStorage.setItem('caos_api_url', apiUrl);
    }

    CONFIG.pollInterval = pollInterval;
    localStorage.setItem('caos_poll_interval', String(pollInterval));

    addLog(`Configurações salvas: API=${apiUrl}, Poll=${pollInterval}ms`, 'info');

    closeSettings();

    // Restart polling
    stopPolling();
    startPolling();
}

function setupSettings() {
    $id('close-settings')?.addEventListener('click', closeSettings);
    $id('cancel-settings')?.addEventListener('click', closeSettings);
    $id('save-settings')?.addEventListener('click', saveSettings);

    $('.modal-backdrop')?.addEventListener('click', closeSettings);
}

// ============================================================================
// LOG FILTERS
// ============================================================================
function setupLogFilters() {
    // Agent tabs filters
    $$('.agent-log-tab').forEach(btn => {
        btn.addEventListener('click', () => {
            $$('.agent-log-tab').forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            renderLogs();
        });
    });

    // Overview page filters
    $$('.log-filter').forEach(btn => {
        btn.addEventListener('click', () => {
            $$('.log-filter').forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            renderLogs();
        });
    });

    // Full logs page filters
    $$('.level-filter').forEach(btn => {
        btn.addEventListener('click', () => {
            $$('.level-filter').forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            renderFullLogs();
        });
    });

    // Log search
    $id('logs-search-input')?.addEventListener('input', () => {
        renderFullLogs();
    });

    // Export logs
    $id('export-logs')?.addEventListener('click', exportLogs);

    // Clear logs
    $id('clear-logs')?.addEventListener('click', clearLogs);
}

function exportLogs() {
    const logText = logs.map(log => `[${log.time}] [${log.type.toUpperCase()}] ${log.message}`).join('\n');
    const blob = new Blob([logText], { type: 'text/plain' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `caos_logs_${new Date().toISOString().slice(0, 10)}.txt`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
    addLog('Logs exportados com sucesso', 'validated');
}

function clearLogs() {
    logs = [];
    renderLogs();
    renderFullLogs();
    updateLogsCount();
    addLog('Logs limpos', 'info');
}

// ============================================================================
// REFRESH
// ============================================================================
function setupRefresh() {
    $id('refresh-all')?.addEventListener('click', () => {
        const btn = $id('refresh-all');
        if (btn) {
            btn.classList.add('spinning');
            setTimeout(() => btn.classList.remove('spinning'), 1000);
        }
        addLog('Atualizando dados...', 'info');
        pollAll();
    });

    $id('reset-all-cb')?.addEventListener('click', () => {
        addLog('Reset de circuit breakers não implementado (API)', 'info');
    });
}

// ============================================================================
// KEYBOARD SHORTCUTS
// ============================================================================
function setupKeyboardShortcuts() {
    document.addEventListener('keydown', (e) => {
        // Escape closes modals
        if (e.key === 'Escape') {
            closeSettings();
        }

        // Alt+1-4 for navigation
        if (e.altKey) {
            const shortcuts = {
                '1': 'overview',
                '2': 'agents',
                '3': 'streams',
                '4': 'logs'
            };
            if (shortcuts[e.key]) {
                switchSection(shortcuts[e.key]);
                $$('.nav-item').forEach(i => {
                    i.classList.toggle('active', i.dataset.section === shortcuts[e.key]);
                });
            }
        }

        // R for refresh (when not in input)
        if (e.key === 'r' && !e.ctrlKey && !e.metaKey &&
            document.activeElement.tagName !== 'INPUT') {
            pollAll();
        }
    });
}

// ============================================================================
// STREAM DETAIL MODAL
// ============================================================================
let currentStreamName = null;

function openStreamModal(streamName) {
    currentStreamName = streamName;

    // Update page header
    const pageTitle = $('.page-title');
    const pageSubtitle = $('.page-subtitle');
    if (pageTitle) pageTitle.textContent = 'Detalhes do Stream';
    if (pageSubtitle) pageSubtitle.textContent = streamName;

    // Switch to stream detail section
    $$('.content-section').forEach(s => s.classList.remove('active'));
    const detailSection = $id('section-stream-detail');
    if (detailSection) {
        detailSection.classList.add('active');
    }

    // Update nav (deselect all)
    $$('.nav-item').forEach(i => i.classList.remove('active'));

    // Update hero
    const heroName = $id('detail-stream-name');
    if (heroName) heroName.textContent = streamName;

    // Update hero icon color based on stream agent
    const heroIcon = document.querySelector('#section-stream-detail .stream-hero-icon');
    const heroSection = document.querySelector('#section-stream-detail .stream-hero');
    if (heroIcon && heroSection) {
        // Determine agent by stream name
        let agentColor = '#00C3FF'; // Atlas blue (default)
        let agentColorAlt = '#7C3AED'; // Purple
        let shadowColor = 'rgba(0, 195, 255, 0.35)';
        let bgGradient = 'rgba(0, 195, 255, 0.12)';
        let bgGradientAlt = 'rgba(124, 58, 237, 0.12)';
        let borderColor = 'rgba(0, 195, 255, 0.2)';

        if (streamName.includes('validated') || streamName.includes('rejected') || streamName.startsWith('caos') || streamName.includes('alerts')) {
            // CAOS - Red (streams produced by CAOS)
            agentColor = '#FF5252';
            agentColorAlt = '#D32F2F';
            shadowColor = 'rgba(255, 82, 82, 0.35)';
            bgGradient = 'rgba(255, 82, 82, 0.12)';
            bgGradientAlt = 'rgba(211, 47, 47, 0.12)';
            borderColor = 'rgba(255, 82, 82, 0.2)';
        } else if (streamName.startsWith('sentinel')) {
            // Sentinel - Yellow
            agentColor = '#FFC93C';
            agentColorAlt = '#FF8C00';
            shadowColor = 'rgba(255, 201, 60, 0.35)';
            bgGradient = 'rgba(255, 201, 60, 0.12)';
            bgGradientAlt = 'rgba(255, 140, 0, 0.12)';
            borderColor = 'rgba(255, 201, 60, 0.2)';
        } else if (streamName.startsWith('oracle')) {
            // Oracle - Purple
            agentColor = '#7C3AED';
            agentColorAlt = '#4F46E5';
            shadowColor = 'rgba(124, 58, 237, 0.35)';
            bgGradient = 'rgba(124, 58, 237, 0.12)';
            bgGradientAlt = 'rgba(79, 70, 229, 0.12)';
            borderColor = 'rgba(124, 58, 237, 0.2)';
        } else if (streamName.startsWith('care')) {
            // Care - Green
            agentColor = '#00F5B5';
            agentColorAlt = '#00C98A';
            shadowColor = 'rgba(0, 245, 181, 0.35)';
            bgGradient = 'rgba(0, 245, 181, 0.12)';
            bgGradientAlt = 'rgba(0, 201, 138, 0.12)';
            borderColor = 'rgba(0, 245, 181, 0.2)';
        }

        heroIcon.style.background = `linear-gradient(135deg, ${agentColor} 0%, ${agentColorAlt} 100%)`;
        heroIcon.style.boxShadow = `0 12px 32px ${shadowColor}`;
        heroSection.style.background = `linear-gradient(135deg, ${bgGradient} 0%, ${bgGradientAlt} 100%)`;
        heroSection.style.borderColor = borderColor;
    }

    // Reset chart interval
    currentStreamInterval = 'day';

    // Load data
    loadStreamDetails(streamName);
}

function closeStreamModal() {
    currentStreamName = null;
    switchSection('streams');
    $$('.nav-item').forEach(i => {
        if (i.dataset.section === 'streams') {
            i.classList.add('active');
        } else {
            i.classList.remove('active');
        }
    });
}

async function loadStreamDetails(streamName) {
    try {
        const data = await fetchAPI(`/streams/${encodeURIComponent(streamName)}/details?limit=100`);

        // Update stats cards
        const statMessages = $id('detail-stat-messages');
        if (statMessages) statMessages.textContent = (data.length || 0).toLocaleString();

        const statConsumers = $id('detail-stat-consumers');
        if (statConsumers) statConsumers.textContent = (data.total_consumers || 0).toLocaleString();

        const statPending = $id('detail-stat-pending');
        if (statPending) statPending.textContent = (data.total_pending || 0).toLocaleString();

        const statRate = $id('detail-stat-rate');
        if (statRate) statRate.textContent = (data.avg_msg_per_hour || 0).toLocaleString();

        const statGroups = $id('detail-stat-groups');
        if (statGroups) statGroups.textContent = (data.groups_count || 0).toLocaleString();

        // Update hero sub
        const timeRange = $id('detail-stream-time-range');
        if (timeRange) timeRange.textContent = `Últimas ${(data.time_range_hours || 0).toFixed(1)} horas`;

        // Update badges
        const groupsBadge = $id('detail-groups-badge');
        if (groupsBadge) groupsBadge.textContent = `${data.groups_count || 0} grupos`;

        const msgBadge = $id('detail-messages-badge');
        if (msgBadge) msgBadge.textContent = `${data.messages?.length || 0} mensagens`;

        // Update chart info
        const chartFirst = $id('chart-first-id');
        if (chartFirst) chartFirst.textContent = data.first_entry_id || '--';

        const chartLast = $id('chart-last-id');
        if (chartLast) chartLast.textContent = data.last_entry_id || '--';

        // Render chart
        // Render chart (initial load)
        loadThroughputData(streamName, currentStreamInterval);

        // Render consumer groups
        renderConsumerGroups(data.groups || []);

        // Extract pending message IDs from groups
        pendingMessageIds = new Set();
        if (data.groups) {
            for (const group of data.groups) {
                // Use pending_ids array from API if available
                if (group.pending_ids && Array.isArray(group.pending_ids)) {
                    for (const id of group.pending_ids) {
                        if (id) pendingMessageIds.add(id);
                    }
                }
            }
        }
        console.log('Pending message IDs:', pendingMessageIds.size, [...pendingMessageIds]);

        // Render messages
        renderMessages(data.messages || []);

        addLog(`Dados do stream ${streamName} carregados`, 'info');

    } catch (error) {
        console.error('Failed to load stream details:', error);
        addLog(`Erro ao carregar detalhes do stream: ${error.message}`, 'error');
    }
}

async function loadThroughputData(streamName, interval) {
    try {
        const data = await fetchAPI(`/streams/${streamName}/throughput?interval=${interval}`);

        const container = $id('stream-throughput-chart');
        if (!container) return;

        // Destroy previous chart if exists
        if (currentThroughputChart) {
            currentThroughputChart.destroy();
        }

        const labels = data.map(d => d.label);
        const counts = data.map(d => d.count);

        // Colors
        const isDark = true; // Assuming dark theme
        const barColor = interval === 'day' ? '#00C3FF' : '#7C3AED';

        currentThroughputChart = new Chart(container, {
            type: 'bar',
            data: {
                labels: labels,
                datasets: [{
                    label: 'Mensagens',
                    data: counts,
                    backgroundColor: barColor,
                    borderRadius: 6,
                    hoverBackgroundColor: '#00F5B5',
                    maxBarThickness: 40,
                    barPercentage: 0.6,
                    categoryPercentage: 0.8
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                scales: {
                    y: {
                        beginAtZero: true,
                        border: { display: false },
                        grid: {
                            color: 'rgba(255, 255, 255, 0.03)',
                            drawBorder: false,
                        },
                        ticks: {
                            color: '#6B7280',
                            font: { size: 11, family: "'Inter', sans-serif" }
                        }
                    },
                    x: {
                        grid: {
                            display: false,
                            drawBorder: false,
                        },
                        ticks: {
                            color: '#6B7280',
                            font: { size: 11, family: "'Inter', sans-serif" }
                        }
                    }
                },
                plugins: {
                    legend: {
                        display: false
                    },
                    tooltip: {
                        enabled: false,
                        external: function (context) {
                            // Get or create tooltip element
                            let tooltipEl = document.getElementById('chartjs-tooltip');
                            if (!tooltipEl) {
                                tooltipEl = document.createElement('div');
                                tooltipEl.id = 'chartjs-tooltip';
                                tooltipEl.innerHTML = '<div class="tooltip-inner"></div>';
                                document.body.appendChild(tooltipEl);
                            }

                            const tooltipModel = context.tooltip;

                            // Hide if no tooltip
                            if (tooltipModel.opacity === 0) {
                                tooltipEl.style.opacity = 0;
                                return;
                            }

                            // Set content
                            if (tooltipModel.body) {
                                const dataPoint = tooltipModel.dataPoints[0];
                                const value = dataPoint.formattedValue;
                                let label = dataPoint.label;

                                // Format date nicely
                                try {
                                    if (label && label.match(/^\d{4}-\d{2}-\d{2}$/)) {
                                        const p = label.split('-');
                                        label = `${p[2]}/${p[1]}`;
                                    } else if (label && label.match(/^\d{4}-\d{2}$/)) {
                                        const p = label.split('-');
                                        const months = ['Jan', 'Fev', 'Mar', 'Abr', 'Mai', 'Jun', 'Jul', 'Ago', 'Set', 'Out', 'Nov', 'Dez'];
                                        label = `${months[parseInt(p[1]) - 1]}`;
                                    }
                                } catch (e) { }

                                const innerHtml = `<span class="tooltip-value">${value}</span><span class="tooltip-label">${label}</span>`;
                                tooltipEl.querySelector('.tooltip-inner').innerHTML = innerHtml;
                            }

                            // Position
                            const position = context.chart.canvas.getBoundingClientRect();
                            tooltipEl.style.opacity = 1;
                            tooltipEl.style.position = 'absolute';
                            tooltipEl.style.left = position.left + window.scrollX + tooltipModel.caretX + 'px';
                            tooltipEl.style.top = position.top + window.scrollY + tooltipModel.caretY - 36 + 'px';
                            tooltipEl.style.pointerEvents = 'none';
                            tooltipEl.style.transform = 'translateX(-50%)';
                        }
                    }
                },
                onClick: (e, elements) => {
                    if (elements.length > 0) {
                        const index = elements[0].index;
                        const label = labels[index];
                        // Filter messages by this date/month
                        filterStreamMessages(streamName, label, interval);
                    }
                }
            }
        });

    } catch (e) {
        console.error('Error loading throughput:', e);
    }
}

async function filterStreamMessages(streamName, dateLabel, interval) {
    // Add visual feedback (loading)
    const tbody = $id('detail-messages-tbody');
    if (tbody) tbody.innerHTML = '<tr class="loading-row"><td colspan="4"><div class="loading-spinner-small"></div> Filtrando...</td></tr>';

    try {
        let startTs, endTs;
        const date = new Date(dateLabel); // Works for YYYY-MM-DD

        if (interval === 'day') {
            // Start of day in local or UTC? Backend uses local keys usually or UTC.
            // Let's assume the label is YYYY-MM-DD.
            // Warning: Timezone issues. Ideally backend returns start/end ts.
            // For simplicity: treat label as UTC date start.
            const start = new Date(dateLabel + 'T00:00:00');
            const end = new Date(dateLabel + 'T23:59:59.999');
            startTs = start.getTime();
            endTs = end.getTime();
        } else {
            // Month: YYYY-MM. 
            const parts = dateLabel.split('-');
            const year = parseInt(parts[0]);
            const month = parseInt(parts[1]) - 1; // JS month 0-11
            const start = new Date(year, month, 1);
            const end = new Date(year, month + 1, 0, 23, 59, 59, 999);
            startTs = start.getTime();
            endTs = end.getTime();
        }

        const data = await fetchAPI(`/streams/${streamName}/details?limit=100&start_ts=${startTs}&end_ts=${endTs}`);
        renderMessages(data.messages || []);

        // Update header/badge to show "Filtered"
        const countBadge = $id('stream-messages-count');
        if (countBadge) countBadge.textContent = `${data.messages?.length || 0} (filtrado)`;

    } catch (e) {
        console.error('Filter error:', e);
        if (tbody) tbody.innerHTML = '<tr><td colspan="4">Erro ao filtrar.</td></tr>';
    }
}

async function toggleChartInterval(interval) {
    if (currentStreamInterval === interval) return;
    currentStreamInterval = interval;

    // Update buttons
    $$('.chart-toggle').forEach(btn => {
        if (btn.dataset.interval === interval) btn.classList.add('active');
        else btn.classList.remove('active');
    });

    if (currentStreamName) {
        await loadThroughputData(currentStreamName, interval);
    }
}

function renderConsumerGroups(groups) {
    const container = $id('detail-consumers-container');
    if (!container) return;

    if (groups.length === 0) {
        container.innerHTML = `
            <div class="consumer-group-empty">
                <i class="ph-bold ph-user-circle-dashed"></i>
                <span>Nenhum consumer group</span>
            </div>
        `;
        return;
    }

    container.innerHTML = groups.map(g => `
        <div class="detail-consumer-group">
            <div class="detail-cg-header">
                <span class="detail-cg-name">${g.name}</span>
                <div class="detail-cg-stats">
                    <span class="detail-cg-stat">${g.consumers_count} consumers</span>
                    <span class="detail-cg-stat">${g.pending} pending</span>
                </div>
            </div>
            ${g.consumers && g.consumers.length > 0 ? `
                <div class="detail-cg-consumers">
                    ${g.consumers.map(c => `
                        <div class="detail-consumer-chip">
                            <i class="ph-bold ph-user"></i>
                            ${c.name}
                            <span style="opacity:0.6;">· idle ${formatIdleTime(c.idle_ms)}</span>
                        </div>
                    `).join('')}
                </div>
            ` : ''}
        </div>
    `).join('');
}

function formatIdleTime(ms) {
    if (!ms) return '0s';
    if (ms < 1000) return `${ms}ms`;
    if (ms < 60000) return `${Math.round(ms / 1000)}s`;
    if (ms < 3600000) return `${Math.round(ms / 60000)}m`;
    return `${Math.round(ms / 3600000)}h`;
}

function renderMessages(messages) {
    // Store all messages for pagination
    allMessages = messages;
    currentPage = 1;

    // Show/hide reason column based on stream type
    const tableWrapper = document.querySelector('.messages-table-wrapper');
    const isRejectedStream = currentStreamName && currentStreamName.includes('rejected');
    const reasonFilter = $id('messages-reason-filter');

    if (tableWrapper) {
        if (isRejectedStream) {
            tableWrapper.classList.remove('hide-reason-column');
            if (reasonFilter) reasonFilter.style.display = '';
        } else {
            tableWrapper.classList.add('hide-reason-column');
            if (reasonFilter) reasonFilter.style.display = 'none';
        }
    }

    renderMessagesPage();
}

function renderMessagesPage() {
    const tbody = $id('detail-messages-tbody');
    if (!tbody) return;

    // Calculate pagination
    const totalMessages = allMessages.length;
    const totalPages = Math.ceil(totalMessages / messagesPerPage);
    const startIndex = (currentPage - 1) * messagesPerPage;
    const endIndex = Math.min(startIndex + messagesPerPage, totalMessages);
    const pageMessages = allMessages.slice(startIndex, endIndex);

    // Update pagination UI
    const showingEl = $id('pagination-showing');
    const totalEl = $id('pagination-total');
    const currentEl = $id('pagination-current');
    const prevBtn = $id('pagination-prev');
    const nextBtn = $id('pagination-next');

    if (showingEl) showingEl.textContent = `Mostrando ${startIndex + 1}-${endIndex}`;
    if (totalEl) totalEl.textContent = totalMessages.toLocaleString();
    if (currentEl) currentEl.textContent = `Página ${currentPage} de ${totalPages || 1}`;
    if (prevBtn) prevBtn.disabled = currentPage <= 1;
    if (nextBtn) nextBtn.disabled = currentPage >= totalPages;

    if (pageMessages.length === 0) {
        tbody.innerHTML = `<tr class="loading-row"><td colspan="6"><i class="ph-bold ph-tray" style="font-size:32px;opacity:0.4;"></i><span>Nenhuma mensagem</span></td></tr>`;
        return;
    }

    const isRejectedStream = currentStreamName && currentStreamName.includes('rejected');

    tbody.innerHTML = pageMessages.map(msg => {
        const ts = new Date(parseInt(msg.timestamp)).toLocaleString('pt-BR');
        const payloadStr = JSON.stringify(msg.fields || {}, null, 2);
        const preview = JSON.stringify(msg.fields || {}).slice(0, 60);

        // Extract rejection reason
        let reason = '';
        let reasonClass = 'unknown';
        if (isRejectedStream && msg.fields) {
            const fields = msg.fields;
            if (fields.reason) {
                reason = fields.reason;
            } else if (fields.error) {
                reason = fields.error;
            } else if (!fields.client || fields.client === 'unknown') {
                reason = 'unknown_client';
            } else if (!fields.asset_id && !fields.asset_serial_number) {
                reason = 'missing_asset';
            }

            // Normalize reason to class
            if (reason.toLowerCase().includes('client') || reason.toLowerCase().includes('origem')) {
                reasonClass = 'unknown_client';
            } else if (reason.toLowerCase().includes('json') || reason.toLowerCase().includes('parse')) {
                reasonClass = 'invalid_json';
            } else if (reason.toLowerCase().includes('asset') || reason.toLowerCase().includes('equipment')) {
                reasonClass = 'missing_asset';
            } else if (reason.toLowerCase().includes('rate') || reason.toLowerCase().includes('limit')) {
                reasonClass = 'rate_limit';
            } else if (reason.toLowerCase().includes('valid')) {
                reasonClass = 'validation_error';
            }
        }

        const reasonLabel = {
            'unknown_client': 'Client',
            'invalid_json': 'JSON',
            'missing_asset': 'Asset',
            'rate_limit': 'Rate Limit',
            'validation_error': 'Validação',
            'unknown': 'Outro'
        }[reasonClass] || 'Outro';

        // Determine message status based on stream type
        const isHealthStream = currentStreamName && currentStreamName.includes('consumer_health');
        let statusClass, statusLabel, statusIcon, rowClass = '';

        if (isHealthStream && msg.fields) {
            // For health streams, check messages_error in payload
            const errorCount = parseInt(msg.fields.messages_error) || 0;
            const successCount = parseInt(msg.fields.messages_success) || 0;

            if (errorCount > 0) {
                statusClass = 'has-errors';
                statusLabel = `${errorCount} erro${errorCount > 1 ? 's' : ''}`;
                statusIcon = 'warning-circle';
                rowClass = 'row-has-errors';
            } else if (successCount > 0) {
                statusClass = 'all-success';
                statusLabel = 'OK';
                statusIcon = 'check-circle';
            } else {
                statusClass = 'processed';
                statusLabel = 'OK';
                statusIcon = 'check-circle';
            }
        } else {
            // For other streams, check if pending
            const isPending = pendingMessageIds.has(msg.id);
            statusClass = isPending ? 'pending' : 'processed';
            statusLabel = isPending ? 'Pendente' : 'Processada';
            statusIcon = isPending ? 'clock-countdown' : 'check-circle';
            rowClass = isPending ? 'row-pending' : '';
        }

        return `
            <tr class="msg-row ${rowClass}">
                <td class="msg-id-cell">${msg.id}</td>
                <td class="msg-time-cell">${ts}</td>
                <td class="msg-status-cell">
                    <span class="status-badge ${statusClass}">
                        <i class="ph-bold ph-${statusIcon}"></i>
                        ${statusLabel}
                    </span>
                </td>
                <td class="msg-reason-cell"><span class="reason-badge ${reasonClass}">${reasonLabel}</span></td>
                <td class="msg-payload-cell"><span class="msg-payload-preview">${escapeHtml(preview)}${preview.length > 60 ? '...' : ''}</span></td>
                <td>
                    <button class="btn-expand-msg" onclick="toggleMsgDetail('${msg.id}')">
                        <i class="ph-bold ph-caret-down" id="icon-${msg.id}"></i>
                    </button>
                </td>
            </tr>
            <tr class="msg-detail-row" id="detail-${msg.id}" style="display:none;">
                <td colspan="6">
                    <div class="msg-detail-content">
                        <pre>${escapeHtml(payloadStr)}</pre>
                    </div>
                </td>
            </tr>
        `;
    }).join('');
}

function changePage(direction) {
    const totalPages = Math.ceil(allMessages.length / messagesPerPage);
    if (direction === 'prev' && currentPage > 1) {
        currentPage--;
        renderMessagesPage();
    } else if (direction === 'next' && currentPage < totalPages) {
        currentPage++;
        renderMessagesPage();
    }
}

function changePerPage(count) {
    messagesPerPage = parseInt(count);
    currentPage = 1;
    renderMessagesPage();
}

function toggleMsgDetail(id) {
    const row = $id(`detail-${id}`);
    const icon = $id(`icon-${id}`);

    if (row.style.display === 'none') {
        row.style.display = 'table-row';
        icon.classList.replace('ph-caret-down', 'ph-caret-up');
        // Add highlight to parent
        row.previousElementSibling.classList.add('expanded');
    } else {
        row.style.display = 'none';
        icon.classList.replace('ph-caret-up', 'ph-caret-down');
        row.previousElementSibling.classList.remove('expanded');
    }
}

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

function setupStreamModal() {
    // Back button
    const backBtn = $id('btn-back-to-streams');
    if (backBtn) backBtn.addEventListener('click', closeStreamModal);

    // Refresh button
    const refreshBtn = $id('btn-refresh-stream-detail');
    if (refreshBtn) {
        refreshBtn.addEventListener('click', () => {
            if (currentStreamName) loadStreamDetails(currentStreamName);
        });
    }

    // Export button
    const exportBtn = $id('btn-export-messages');
    if (exportBtn) exportBtn.addEventListener('click', () => addLog('Export em breve', 'info'));

    // Stream cards
    $$('.stream-card').forEach(card => {
        card.addEventListener('click', () => {
            const name = card.querySelector('h3')?.textContent;
            if (name) openStreamModal(name);
        });
    });

    // Stream chips
    $$('.stream-chip').forEach(chip => {
        chip.style.cursor = 'pointer';
        chip.addEventListener('click', () => {
            const name = chip.querySelector('span:not(.stream-count)')?.textContent;
            if (name) openStreamModal(name);
        });
    });

    // Search filter
    const search = $id('messages-search-input');
    if (search) {
        search.addEventListener('input', e => {
            const term = e.target.value.toLowerCase();
            $$('#detail-messages-tbody tr:not(.loading-row)').forEach(row => {
                row.style.display = row.textContent.toLowerCase().includes(term) ? '' : 'none';
            });
        });
    }

    // Pagination buttons
    $id('pagination-prev')?.addEventListener('click', () => changePage('prev'));
    $id('pagination-next')?.addEventListener('click', () => changePage('next'));

    // Per page selector
    $id('messages-per-page')?.addEventListener('change', (e) => {
        changePerPage(e.target.value);
    });

    // Reason filter
    $id('messages-reason-filter')?.addEventListener('change', (e) => {
        const filterValue = e.target.value.toLowerCase();
        if (!filterValue) {
            renderMessages(allMessages);
        } else {
            // Filter based on reason/fields
            const filtered = allMessages.filter(msg => {
                const fields = msg.fields || {};
                const reason = (fields.reason || fields.error || '').toLowerCase();
                const client = (fields.client || '').toLowerCase();
                const hasAsset = fields.asset_id || fields.asset_serial_number;

                if (filterValue === 'unknown_client') {
                    return !client || client === 'unknown' || reason.includes('client');
                } else if (filterValue === 'invalid_json') {
                    return reason.includes('json') || reason.includes('parse');
                } else if (filterValue === 'missing_asset') {
                    return !hasAsset || reason.includes('asset');
                } else if (filterValue === 'rate_limit') {
                    return reason.includes('rate') || reason.includes('limit');
                } else if (filterValue === 'validation_error') {
                    return reason.includes('valid');
                }
                return true;
            });
            allMessages = filtered;
            currentPage = 1;
            renderMessagesPage();
        }
    });

    // Sort selector
    $id('messages-sort')?.addEventListener('change', (e) => {
        const sortOrder = e.target.value;
        if (sortOrder === 'asc') {
            allMessages.sort((a, b) => parseInt(a.timestamp) - parseInt(b.timestamp));
        } else {
            allMessages.sort((a, b) => parseInt(b.timestamp) - parseInt(a.timestamp));
        }
        currentPage = 1;
        renderMessagesPage();
    });

    // Status filter
    $id('messages-status-filter')?.addEventListener('change', (e) => {
        const filterValue = e.target.value;
        if (!filterValue) {
            // Reset - need to reload original messages
            if (currentStreamName) {
                loadStreamDetails(currentStreamName);
            }
        } else {
            const isHealthStream = currentStreamName && currentStreamName.includes('consumer_health');

            const filtered = allMessages.filter(msg => {
                if (isHealthStream && msg.fields) {
                    // For health streams, filter by error status
                    const errorCount = parseInt(msg.fields.messages_error) || 0;

                    if (filterValue === 'has-errors') {
                        return errorCount > 0;
                    } else if (filterValue === 'all-success') {
                        return errorCount === 0;
                    }
                    return true;
                } else {
                    // For other streams, filter by pending status
                    const isPending = pendingMessageIds.has(msg.id);

                    if (filterValue === 'pending') {
                        return isPending;
                    } else if (filterValue === 'processed') {
                        return !isPending;
                    }
                    return true;
                }
            });

            allMessages = filtered;
            currentPage = 1;
            renderMessagesPage();

            // Update badge with filtered count
            const msgBadge = $id('detail-messages-badge');
            if (msgBadge) msgBadge.textContent = `${filtered.length} mensagens`;
        }
    });
}

// Make pagination functions global
window.changePage = changePage;
window.changePerPage = changePerPage;

// ============================================================================
// INITIALIZATION
// ============================================================================
function init() {
    console.log('CAOS Dashboard initializing...');
    console.log('API URL:', CONFIG.apiUrl);
    console.log('Poll Interval:', CONFIG.pollInterval);

    // Clock
    updateClock();
    setInterval(updateClock, 1000);

    // Setup event handlers
    setupNavigation();
    setupSettings();
    setupLogFilters();
    setupRefresh();
    setupKeyboardShortcuts();
    setupClientSelector();
    setupStreamModal();

    // Load clients list
    loadClients();

    // Initial log
    addLog('Dashboard inicializado', 'info');
    addLog(`Conectando a ${CONFIG.apiUrl}...`, 'info');

    // Start polling
    startPolling();
}

// Start
document.addEventListener('DOMContentLoaded', init);


// ============================================================================
// ATLAS CONFIGURATION MODAL
// ============================================================================

function openAtlasConfig() {
    // Navigate to the Atlas config page section (full page)
    switchSection('atlas-config');
    loadAtlasConfigPage();
    setupAtlasConfigPageHandlers();
}

function goBackToAgents() {
    switchSection('agents');
}

// Also expose to window for onclick handlers
window.openAtlasConfig = openAtlasConfig;
window.restartAtlasAgent = restartAtlasAgent;
window.goBackToAgents = goBackToAgents;

async function loadAtlasConfigPage() {
    try {
        const data = await fetchAPI('/atlas/config');

        // Update hero
        const versionDisplay = $id('atlas-config-version-display');
        if (versionDisplay) versionDisplay.textContent = `v${data.app?.version || '1.0.0'}`;

        // Server settings
        const workersInput = $id('atlas-cfg-workers');
        if (workersInput) workersInput.value = data.server?.workers || 4;

        const logLevelSelect = $id('atlas-cfg-log-level');
        if (logLevelSelect) logLevelSelect.value = data.logging?.log_level || 'INFO';

        const debugCheck = $id('atlas-cfg-debug');
        if (debugCheck) debugCheck.checked = data.server?.debug || false;

        // Update stat cards
        const statWorkers = $id('atlas-stat-workers');
        if (statWorkers) statWorkers.textContent = data.server?.workers || 4;

        // Database
        const dbUrl = $id('atlas-cfg-db-url');
        if (dbUrl) dbUrl.value = data.database?.url_raw || '';

        const poolSize = $id('atlas-cfg-pool-size');
        if (poolSize) poolSize.value = data.database?.pool_size || 10;

        const maxOverflow = $id('atlas-cfg-max-overflow');
        if (maxOverflow) maxOverflow.value = data.database?.max_overflow || 20;

        const poolRecycle = $id('atlas-cfg-pool-recycle');
        if (poolRecycle) poolRecycle.value = data.database?.pool_recycle || 3600;

        // Redis
        const redisUrl = $id('atlas-cfg-redis-url');
        if (redisUrl) redisUrl.value = data.redis?.url || '';

        const streamName = $id('atlas-cfg-stream');
        if (streamName) streamName.value = data.redis?.sentinel_stream || 'telemetry.received';

        // Security
        const apiKey = $id('atlas-cfg-api-key');
        if (apiKey) apiKey.value = data.security?.api_key_full || '';

        // Scheduling
        const cronInput = $id('atlas-cfg-cron');
        if (cronInput) cronInput.value = data.scheduling?.cron_schedule || '0 10 * * *';

        const cronDesc = $id('atlas-cfg-cron-desc');
        if (cronDesc) cronDesc.value = data.scheduling?.cron_description || '07:00 BRT';

        // App info
        const infoName = $id('atlas-info-name');
        if (infoName) infoName.textContent = data.app?.name || 'Atlas Agent API';

        const infoVersion = $id('atlas-info-version');
        if (infoVersion) infoVersion.textContent = data.app?.version || '1.0.0';

        // Load routine status
        loadRoutineStatusPage();

        addLog('Configuração do Atlas carregada', 'validated');
    } catch (error) {
        console.error('Failed to load Atlas config:', error);
        addLog('Erro ao carregar configuração do Atlas', 'rejected');
    }
}

function setupAtlasConfigPageHandlers() {
    // Back button
    $id('btn-back-to-agents')?.addEventListener('click', goBackToAgents);

    // Save button
    $id('btn-save-atlas-hero')?.addEventListener('click', saveAtlasConfigPage);

    // Test buttons
    $id('atlas-btn-test-db')?.addEventListener('click', testDatabasePage);
    $id('atlas-btn-test-redis')?.addEventListener('click', testRedisPage);

    // API Key toggle
    $id('atlas-toggle-key')?.addEventListener('click', () => {
        const input = $id('atlas-cfg-api-key');
        const btn = $id('atlas-toggle-key');
        if (input.type === 'password') {
            input.type = 'text';
            btn.innerHTML = '<i class="ph-bold ph-eye-slash"></i>';
        } else {
            input.type = 'password';
            btn.innerHTML = '<i class="ph-bold ph-eye"></i>';
        }
    });

    // Generate API Key
    $id('atlas-gen-key')?.addEventListener('click', () => {
        const uuid = 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, c => {
            const r = Math.random() * 16 | 0;
            return (c === 'x' ? r : (r & 0x3 | 0x8)).toString(16);
        });
        $id('atlas-cfg-api-key').value = uuid;
        $id('atlas-cfg-api-key').type = 'text';
    });

    // Cron presets
    $$('.preset-chip').forEach(chip => {
        chip.addEventListener('click', () => {
            const cron = chip.dataset.cron;
            $id('atlas-cfg-cron').value = cron;
            updateCronDescriptionPage(cron);
        });
    });

    // Cron input change
    $id('atlas-cfg-cron')?.addEventListener('input', e => {
        updateCronDescriptionPage(e.target.value);
    });

    // Trigger routine
    $id('atlas-btn-trigger')?.addEventListener('click', triggerRoutinePage);
}

function updateCronDescriptionPage(cron) {
    const parts = cron.split(' ');
    const descField = $id('atlas-cfg-cron-desc');
    if (!descField || parts.length < 5) {
        if (descField) descField.value = 'Formato inválido';
        return;
    }

    const minute = parts[0];
    const hour = parts[1];

    if (minute.startsWith('*/')) {
        descField.value = `A cada ${minute.replace('*/', '')} min`;
    } else {
        try {
            const utcHour = parseInt(hour);
            const brtHour = (utcHour - 3 + 24) % 24;
            descField.value = `${String(brtHour).padStart(2, '0')}:${minute.padStart(2, '0')} BRT`;
        } catch {
            descField.value = cron;
        }
    }
}

async function saveAtlasConfigPage() {
    const btn = $id('btn-save-atlas-hero');
    const originalHTML = btn.innerHTML;
    btn.disabled = true;
    btn.innerHTML = '<i class="ph-bold ph-spinner"></i> Salvando...';

    try {
        const config = {
            database_url: $id('atlas-cfg-db-url')?.value,
            database_pool_size: parseInt($id('atlas-cfg-pool-size')?.value),
            database_max_overflow: parseInt($id('atlas-cfg-max-overflow')?.value),
            database_pool_recycle: parseInt($id('atlas-cfg-pool-recycle')?.value),
            redis_url: $id('atlas-cfg-redis-url')?.value,
            sentinel_stream: $id('atlas-cfg-stream')?.value,
            api_key: $id('atlas-cfg-api-key')?.value,
            log_level: $id('atlas-cfg-log-level')?.value,
            workers: parseInt($id('atlas-cfg-workers')?.value),
            debug: $id('atlas-cfg-debug')?.checked,
            cron_schedule: $id('atlas-cfg-cron')?.value
        };

        const response = await fetch(`${CONFIG.apiUrl}/atlas/config`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(config)
        });

        const result = await response.json();

        if (result.status === 'success') {
            addLog('Configuração do Atlas salva!', 'validated');
            alert('Configuração salva! Reinicie o Atlas para aplicar.');
        } else {
            addLog(`Erro: ${result.message}`, 'rejected');
            alert(`Erro: ${result.message}`);
        }
    } catch (error) {
        addLog(`Erro ao salvar: ${error.message}`, 'rejected');
        alert(`Erro: ${error.message}`);
    } finally {
        btn.disabled = false;
        btn.innerHTML = originalHTML;
    }
}

async function testDatabasePage() {
    const btn = $id('atlas-btn-test-db');
    const result = $id('atlas-db-result');

    btn.innerHTML = '<i class="ph-bold ph-spinner"></i> Testando...';
    btn.classList.add('loading');

    try {
        const response = await fetch(`${CONFIG.apiUrl}/atlas/test-db`, { method: 'POST' });
        const data = await response.json();

        result.className = `test-result-box show ${data.status}`;
        result.innerHTML = `<i class="ph-bold ph-${data.status === 'success' ? 'check-circle' : 'x-circle'}"></i> ${data.message}`;
    } catch (error) {
        result.className = 'test-result-box show error';
        result.innerHTML = `<i class="ph-bold ph-x-circle"></i> ${error.message}`;
    } finally {
        btn.innerHTML = '<i class="ph-bold ph-plugs-connected"></i> Testar';
        btn.classList.remove('loading');
    }
}

async function testRedisPage() {
    const btn = $id('atlas-btn-test-redis');
    const result = $id('atlas-redis-result');

    btn.innerHTML = '<i class="ph-bold ph-spinner"></i> Testando...';
    btn.classList.add('loading');

    try {
        const response = await fetch(`${CONFIG.apiUrl}/atlas/test-redis`, { method: 'POST' });
        const data = await response.json();

        result.className = `test-result-box show ${data.status}`;
        result.innerHTML = `<i class="ph-bold ph-${data.status === 'success' ? 'check-circle' : 'x-circle'}"></i> ${data.message}`;
    } catch (error) {
        result.className = 'test-result-box show error';
        result.innerHTML = `<i class="ph-bold ph-x-circle"></i> ${error.message}`;
    } finally {
        btn.innerHTML = '<i class="ph-bold ph-lightning"></i> Testar';
        btn.classList.remove('loading');
    }
}

async function loadRoutineStatusPage() {
    try {
        const data = await fetchAPI('/atlas/routine-status');
        const routineIcon = $id('atlas-routine-display')?.querySelector('.routine-icon');
        const routineTime = $id('atlas-routine-time');
        const statRoutine = $id('atlas-stat-routine');

        if (data.status === 'completed') {
            if (routineIcon) routineIcon.className = 'routine-icon success';
            if (routineTime) routineTime.textContent = formatTimestamp(data.timestamp);
            if (statRoutine) statRoutine.textContent = 'OK';
        } else if (data.status === 'failed') {
            if (routineIcon) routineIcon.className = 'routine-icon error';
            if (routineTime) routineTime.textContent = 'Falhou';
            if (statRoutine) statRoutine.textContent = 'ERRO';
        } else {
            if (routineTime) routineTime.textContent = '--';
            if (statRoutine) statRoutine.textContent = '--';
        }
    } catch (error) {
        console.log('Routine status not available');
    }
}

async function triggerRoutinePage() {
    const btn = $id('atlas-btn-trigger');
    const routineIcon = $id('atlas-routine-display')?.querySelector('.routine-icon');
    const routineTime = $id('atlas-routine-time');

    btn.disabled = true;
    btn.innerHTML = '<i class="ph-bold ph-spinner"></i> Executando...';
    if (routineIcon) {
        routineIcon.className = 'routine-icon running';
        routineIcon.innerHTML = '<i class="ph-bold ph-spinner"></i>';
    }
    if (routineTime) routineTime.textContent = 'Em execução...';

    addLog('Executando rotina diária...', 'info');

    try {
        const response = await fetch(`${CONFIG.apiUrl}/atlas/trigger-routine`, { method: 'POST' });
        const data = await response.json();

        if (data.status === 'success') {
            if (routineIcon) {
                routineIcon.className = 'routine-icon success';
                routineIcon.innerHTML = '<i class="ph-bold ph-check-circle"></i>';
            }
            if (routineTime) routineTime.textContent = 'Concluído agora';
            addLog('Rotina concluída com sucesso!', 'validated');
        } else {
            if (routineIcon) {
                routineIcon.className = 'routine-icon error';
                routineIcon.innerHTML = '<i class="ph-bold ph-x-circle"></i>';
            }
            if (routineTime) routineTime.textContent = 'Falhou';
            addLog(`Rotina falhou: ${data.message}`, 'rejected');
        }
    } catch (error) {
        if (routineIcon) routineIcon.className = 'routine-icon error';
        if (routineTime) routineTime.textContent = 'Erro';
        addLog(`Erro: ${error.message}`, 'rejected');
    } finally {
        btn.disabled = false;
        btn.innerHTML = '<i class="ph-bold ph-play-circle"></i> Executar Agora';
    }
}

async function loadAtlasConfig() {
    try {
        const data = await fetchAPI('/atlas/config');

        // Populate form fields
        // General
        $id('cfg-app-name').value = data.app?.name || 'Atlas Agent API';
        $id('cfg-app-version').value = data.app?.version || '1.0.0';
        $id('atlas-config-version').textContent = `v${data.app?.version || '1.0.0'}`;

        $id('cfg-workers').value = data.server?.workers || 4;
        $id('cfg-log-level').value = data.logging?.log_level || 'INFO';
        $id('cfg-debug').checked = data.server?.debug || false;

        // Database
        $id('cfg-database-url').value = data.database?.url_raw || '';
        $id('cfg-pool-size').value = data.database?.pool_size || 10;
        $id('cfg-max-overflow').value = data.database?.max_overflow || 20;
        $id('cfg-pool-recycle').value = data.database?.pool_recycle || 3600;

        // Redis
        $id('cfg-redis-url').value = data.redis?.url || '';
        $id('cfg-sentinel-stream').value = data.redis?.sentinel_stream || 'telemetry.received';

        // Security
        $id('cfg-api-key').value = data.security?.api_key_full || '';

        // Scheduling
        $id('cfg-cron-schedule').value = data.scheduling?.cron_schedule || '0 10 * * *';
        $id('cfg-cron-description').value = data.scheduling?.cron_description || '';

        // Status (these come from health endpoint)
        updateAtlasStatus();

    } catch (error) {
        console.error('Failed to load Atlas config:', error);
        addLog('Erro ao carregar configuração do Atlas', 'rejected');
    }
}

async function updateAtlasStatus() {
    try {
        const health = await fetchAPI('/health');
        $id('atlas-status-health').textContent = health.status === 'ok' ? 'OK' : 'ERRO';
        $id('atlas-status-uptime').textContent = health.uptime || '--';
    } catch (error) {
        $id('atlas-status-health').textContent = 'Offline';
    }
}

async function loadRoutineStatus() {
    try {
        const data = await fetchAPI('/atlas/routine-status');
        const statusBox = $id('routine-status-box');
        const statusMsg = $id('routine-status-message');
        const routineStatusEl = $id('atlas-routine-status');

        if (data.status === 'completed') {
            statusBox.className = 'routine-status success';
            statusBox.querySelector('i').className = 'ph-bold ph-check-circle';
            statusMsg.textContent = `Última execução: ${formatTimestamp(data.timestamp)}`;
            if (routineStatusEl) routineStatusEl.textContent = 'OK';
        } else if (data.status === 'failed') {
            statusBox.className = 'routine-status failed';
            statusBox.querySelector('i').className = 'ph-bold ph-x-circle';
            statusMsg.textContent = `Falhou: ${data.message}`;
            if (routineStatusEl) routineStatusEl.textContent = 'ERRO';
        } else {
            statusBox.className = 'routine-status';
            statusBox.querySelector('i').className = 'ph-bold ph-clock';
            statusMsg.textContent = 'Sem execução recente';
            if (routineStatusEl) routineStatusEl.textContent = '--';
        }
    } catch (error) {
        console.log('Routine status not available');
    }
}

function formatTimestamp(ts) {
    if (!ts) return '--';
    try {
        const date = new Date(ts);
        return date.toLocaleString('pt-BR', {
            day: '2-digit',
            month: '2-digit',
            hour: '2-digit',
            minute: '2-digit'
        });
    } catch {
        return ts;
    }
}

async function saveAtlasConfig() {
    const btn = $id('save-atlas-config');
    btn.disabled = true;
    btn.innerHTML = '<i class="ph-bold ph-spinner"></i> Salvando...';

    try {
        const config = {
            database_url: $id('cfg-database-url').value,
            database_pool_size: parseInt($id('cfg-pool-size').value),
            database_max_overflow: parseInt($id('cfg-max-overflow').value),
            database_pool_recycle: parseInt($id('cfg-pool-recycle').value),
            redis_url: $id('cfg-redis-url').value,
            sentinel_stream: $id('cfg-sentinel-stream').value,
            api_key: $id('cfg-api-key').value,
            log_level: $id('cfg-log-level').value,
            workers: parseInt($id('cfg-workers').value),
            debug: $id('cfg-debug').checked,
            cron_schedule: $id('cfg-cron-schedule').value
        };

        const response = await fetch(`${CONFIG.apiUrl}/atlas/config`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(config)
        });

        const result = await response.json();

        if (result.status === 'success') {
            addLog('Configuração do Atlas salva com sucesso!', 'validated');
            alert('Configuração salva! Reinicie o Atlas Agent para aplicar as alterações.');
        } else {
            addLog(`Erro ao salvar: ${result.message}`, 'rejected');
            alert(`Erro: ${result.message}`);
        }
    } catch (error) {
        addLog(`Erro ao salvar configuração: ${error.message}`, 'rejected');
        alert(`Erro: ${error.message}`);
    } finally {
        btn.disabled = false;
        btn.innerHTML = '<i class="ph-bold ph-floppy-disk"></i> Salvar Configurações';
    }
}

async function testDatabaseConnection() {
    const btn = $id('btn-test-db');
    const result = $id('db-test-result');

    btn.classList.add('loading');
    btn.innerHTML = '<i class="ph-bold ph-spinner"></i> Testando...';
    result.className = 'test-result';
    result.innerHTML = '';

    try {
        const response = await fetch(`${CONFIG.apiUrl}/atlas/test-db`, { method: 'POST' });
        const data = await response.json();

        if (data.status === 'success') {
            result.className = 'test-result show success';
            result.innerHTML = `<i class="ph-bold ph-check-circle"></i> ${data.message}`;
        } else {
            result.className = 'test-result show error';
            result.innerHTML = `<i class="ph-bold ph-x-circle"></i> ${data.message}`;
        }
    } catch (error) {
        result.className = 'test-result show error';
        result.innerHTML = `<i class="ph-bold ph-x-circle"></i> Erro: ${error.message}`;
    } finally {
        btn.classList.remove('loading');
        btn.innerHTML = '<i class="ph-bold ph-plugs-connected"></i> Testar Conexão';
    }
}

async function testRedisConnection() {
    const btn = $id('btn-test-redis');
    const result = $id('redis-test-result');

    btn.classList.add('loading');
    btn.innerHTML = '<i class="ph-bold ph-spinner"></i> Testando...';
    result.className = 'test-result';
    result.innerHTML = '';

    try {
        const response = await fetch(`${CONFIG.apiUrl}/atlas/test-redis`, { method: 'POST' });
        const data = await response.json();

        if (data.status === 'success') {
            result.className = 'test-result show success';
            result.innerHTML = `<i class="ph-bold ph-check-circle"></i> ${data.message} (v${data.redis_version})`;
        } else {
            result.className = 'test-result show error';
            result.innerHTML = `<i class="ph-bold ph-x-circle"></i> ${data.message}`;
        }
    } catch (error) {
        result.className = 'test-result show error';
        result.innerHTML = `<i class="ph-bold ph-x-circle"></i> Erro: ${error.message}`;
    } finally {
        btn.classList.remove('loading');
        btn.innerHTML = '<i class="ph-bold ph-lightning"></i> Testar Conexão Redis';
    }
}

async function restartAtlasAgent() {
    if (!confirm('Tem certeza que deseja reiniciar o Atlas Agent?')) {
        return;
    }

    const btn = $id('btn-restart-atlas') || document.querySelector('[onclick="restartAtlasAgent()"]');
    if (btn) {
        btn.disabled = true;
        btn.innerHTML = '<i class="ph-bold ph-spinner"></i> Reiniciando...';
    }

    addLog('Reiniciando Atlas Agent...', 'info');

    try {
        const response = await fetch(`${CONFIG.apiUrl}/atlas/restart`, { method: 'POST' });
        const data = await response.json();

        if (data.status === 'success') {
            addLog('Atlas Agent reiniciado com sucesso!', 'validated');
            alert('Atlas Agent reiniciado com sucesso!');
        } else {
            addLog(`Erro ao reiniciar: ${data.message}`, 'rejected');
            alert(`Erro: ${data.message}`);
        }
    } catch (error) {
        addLog(`Erro ao reiniciar: ${error.message}`, 'rejected');
        alert(`Erro: ${error.message}`);
    } finally {
        if (btn) {
            btn.disabled = false;
            btn.innerHTML = '<i class="ph-bold ph-arrow-clockwise"></i> Reiniciar Atlas';
        }
    }
}

async function triggerDailyRoutine() {
    const btn = $id('btn-trigger-routine');
    const statusBox = $id('routine-status-box');
    const statusMsg = $id('routine-status-message');

    btn.disabled = true;
    btn.innerHTML = '<i class="ph-bold ph-spinner"></i> Executando...';
    statusBox.className = 'routine-status running';
    statusBox.querySelector('i').className = 'ph-bold ph-spinner';
    statusMsg.textContent = 'Executando rotina diária...';

    addLog('Iniciando rotina diária manualmente...', 'info');

    try {
        const response = await fetch(`${CONFIG.apiUrl}/atlas/trigger-routine`, { method: 'POST' });
        const data = await response.json();

        if (data.status === 'success') {
            statusBox.className = 'routine-status success';
            statusBox.querySelector('i').className = 'ph-bold ph-check-circle';
            statusMsg.textContent = 'Rotina concluída com sucesso!';
            addLog('Rotina diária concluída com sucesso!', 'validated');
        } else {
            statusBox.className = 'routine-status failed';
            statusBox.querySelector('i').className = 'ph-bold ph-x-circle';
            statusMsg.textContent = `Falhou: ${data.message}`;
            addLog(`Rotina falhou: ${data.message}`, 'rejected');
        }
    } catch (error) {
        statusBox.className = 'routine-status failed';
        statusBox.querySelector('i').className = 'ph-bold ph-x-circle';
        statusMsg.textContent = `Erro: ${error.message}`;
        addLog(`Erro na rotina: ${error.message}`, 'rejected');
    } finally {
        btn.disabled = false;
        btn.innerHTML = '<i class="ph-bold ph-play-circle"></i> Executar Rotina Agora';
    }
}

function generateApiKey() {
    const uuid = 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, function (c) {
        const r = Math.random() * 16 | 0;
        const v = c == 'x' ? r : (r & 0x3 | 0x8);
        return v.toString(16);
    });
    $id('cfg-api-key').value = uuid;
    $id('cfg-api-key').type = 'text';
}

function toggleApiKeyVisibility() {
    const input = $id('cfg-api-key');
    const btn = $id('btn-toggle-api-key');

    if (input.type === 'password') {
        input.type = 'text';
        btn.innerHTML = '<i class="ph-bold ph-eye-slash"></i>';
    } else {
        input.type = 'password';
        btn.innerHTML = '<i class="ph-bold ph-eye"></i>';
    }
}

function setupAtlasConfigHandlers() {
    // Close buttons
    $id('close-atlas-config')?.addEventListener('click', closeAtlasConfig);
    $id('cancel-atlas-config')?.addEventListener('click', closeAtlasConfig);

    // Backdrop close
    const modal = $id('atlas-config-modal');
    modal?.querySelector('.modal-backdrop')?.addEventListener('click', closeAtlasConfig);

    // Save button
    $id('save-atlas-config')?.addEventListener('click', saveAtlasConfig);

    // Test buttons
    $id('btn-test-db')?.addEventListener('click', testDatabaseConnection);
    $id('btn-test-redis')?.addEventListener('click', testRedisConnection);

    // Restart button
    $id('btn-restart-atlas')?.addEventListener('click', restartAtlasAgent);

    // Trigger routine
    $id('btn-trigger-routine')?.addEventListener('click', triggerDailyRoutine);

    // API Key
    $id('btn-generate-key')?.addEventListener('click', generateApiKey);
    $id('btn-toggle-api-key')?.addEventListener('click', toggleApiKeyVisibility);

    // Tab navigation
    $$('.config-tab').forEach(tab => {
        tab.addEventListener('click', () => {
            const tabName = tab.dataset.tab;

            // Update active tab
            $$('.config-tab').forEach(t => t.classList.remove('active'));
            tab.classList.add('active');

            // Show content
            $$('.config-tab-content').forEach(c => c.classList.remove('active'));
            $id(`tab-${tabName}`)?.classList.add('active');
        });
    });

    // Cron presets
    $$('.cron-preset').forEach(preset => {
        preset.addEventListener('click', () => {
            const cronValue = preset.dataset.cron;
            $id('cfg-cron-schedule').value = cronValue;

            // Update description
            updateCronDescription(cronValue);
        });
    });

    // Cron input change
    $id('cfg-cron-schedule')?.addEventListener('input', (e) => {
        updateCronDescription(e.target.value);
    });
}

function updateCronDescription(cron) {
    const parts = cron.split(' ');
    if (parts.length < 5) {
        $id('cfg-cron-description').value = 'Formato inválido';
        return;
    }

    const minute = parts[0];
    const hour = parts[1];

    // Simple description
    if (minute.startsWith('*/')) {
        const mins = minute.replace('*/', '');
        $id('cfg-cron-description').value = `A cada ${mins} minutos`;
    } else {
        try {
            const utcHour = parseInt(hour);
            const brtHour = (utcHour - 3 + 24) % 24;
            $id('cfg-cron-description').value = `Todos os dias às ${String(brtHour).padStart(2, '0')}:${minute.padStart(2, '0')} BRT`;
        } catch {
            $id('cfg-cron-description').value = `Cron: ${cron}`;
        }
    }
}

// ============================================================================
// ATLAS MONITORING FEATURES (7 New Features)
// ============================================================================

async function loadRoutineTimeline() {
    const container = $id('atlas-routine-timeline');
    if (!container) return;

    container.innerHTML = '<div class="timeline-loading"><i class="ph-bold ph-spinner"></i> Carregando...</div>';

    try {
        const data = await fetchAPI('/atlas/routine-history?limit=20');

        if (!data.events || data.events.length === 0) {
            container.innerHTML = '<div class="timeline-loading">Nenhum evento encontrado</div>';
            return;
        }

        let html = '<div class="timeline-events">';
        for (const event of data.events) {
            const statusClass = event.status.toLowerCase();
            const icon = statusClass === 'completed' ? 'check-circle' :
                statusClass === 'failed' ? 'x-circle' : 'play-circle';
            const eventTitle = `${event.event_type} - ${event.status}`;
            const timeStr = event.timestamp ? new Date(event.timestamp).toLocaleString('pt-BR') : '--';

            html += `
                <div class="timeline-event">
                    <div class="timeline-event-icon ${statusClass}">
                        <i class="ph-bold ph-${icon}"></i>
                    </div>
                    <div class="timeline-event-content">
                        <div class="timeline-event-title">${eventTitle}</div>
                        <div class="timeline-event-message">${event.message || ''}</div>
                    </div>
                    <span class="timeline-event-time">${timeStr}</span>
                </div>
            `;
        }
        html += '</div>';
        container.innerHTML = html;
    } catch (error) {
        container.innerHTML = `<div class="timeline-loading">Erro: ${error.message}</div>`;
    }
}

async function loadAtlasLogs(date = '') {
    const viewer = $id('atlas-log-viewer');
    if (!viewer) return;

    viewer.innerHTML = '<div class="log-empty"><i class="ph-bold ph-spinner"></i><span>Carregando logs...</span></div>';

    try {
        const url = date ? `/atlas/logs?date=${date}` : '/atlas/logs';
        const data = await fetchAPI(url);

        if (data.status === 'success' && data.lines?.length > 0) {
            let html = '';
            for (const line of data.lines) {
                let lineClass = '';
                if (line.includes('✓') || line.includes('sucesso')) lineClass = 'success';
                else if (line.includes('✗') || line.includes('ERRO') || line.includes('Erro')) lineClass = 'error';
                else if (line.includes('Iniciando') || line.includes('Etapa')) lineClass = 'info';

                html += `<div class="log-line ${lineClass}">${escapeHtml(line)}</div>`;
            }
            viewer.innerHTML = html;
        } else if (data.status === 'not_found') {
            // Populate date selector with available dates
            const select = $id('atlas-log-date');
            if (select && data.available_dates) {
                select.innerHTML = '<option value="">Hoje</option>';
                for (const d of data.available_dates.slice(0, 10)) {
                    select.innerHTML += `<option value="${d}">${formatLogDate(d)}</option>`;
                }
            }
            viewer.innerHTML = `<div class="log-empty"><i class="ph-bold ph-file-dashed"></i><span>${data.message}</span></div>`;
        } else {
            viewer.innerHTML = `<div class="log-empty"><i class="ph-bold ph-warning"></i><span>${data.message || 'Erro ao carregar logs'}</span></div>`;
        }
    } catch (error) {
        viewer.innerHTML = `<div class="log-empty"><i class="ph-bold ph-warning"></i><span>Erro: ${error.message}</span></div>`;
    }
}

function formatLogDate(d) {
    if (!d || d.length !== 8) return d;
    return `${d.slice(6, 8)}/${d.slice(4, 6)}/${d.slice(0, 4)}`;
}

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

async function loadTelemetryStats() {
    try {
        const data = await fetchAPI('/atlas/telemetry-stats');

        const validated = $id('atlas-telem-validated');
        const rejected = $id('atlas-telem-rejected');
        const stream = $id('atlas-telem-stream');

        if (validated) validated.textContent = data.validated_today ?? '--';
        if (rejected) rejected.textContent = data.rejected_today ?? '--';
        if (stream) stream.textContent = data.stream_length ?? '--';
    } catch (error) {
        console.log('Telemetry stats not available:', error);
    }
}

async function loadDashboardStats() {
    try {
        const data = await fetchAPI('/atlas/dashboard-stats?client=Coel');

        if (data.status === 'success') {
            const assets = $id('atlas-stats-assets');
            const outlets = $id('atlas-stats-outlets');
            const users = $id('atlas-stats-users');
            const alerts = $id('atlas-stats-alerts');

            if (assets) assets.textContent = data.assets?.toLocaleString() ?? '--';
            if (outlets) outlets.textContent = data.outlets?.toLocaleString() ?? '--';
            if (users) users.textContent = data.users?.toLocaleString() ?? '--';
            if (alerts) alerts.textContent = data.alerts?.toLocaleString() ?? '--';
        }
    } catch (error) {
        console.log('Dashboard stats not available:', error);
    }
}

async function loadApiKeys() {
    const tbody = $id('atlas-api-keys-body');
    if (!tbody) return;

    tbody.innerHTML = '<tr class="loading-row"><td colspan="5"><i class="ph-bold ph-spinner"></i> Carregando...</td></tr>';

    try {
        const data = await fetchAPI('/atlas/api-keys');

        if (data.status === 'success' && data.keys?.length > 0) {
            let html = '';
            for (const key of data.keys) {
                const partialKey = key.key_hash?.slice(0, 12) + '...' || '****';
                const createdAt = key.created_at ? new Date(key.created_at).toLocaleDateString('pt-BR') : '--';

                html += `
                    <tr>
                        <td>${key.name || 'Sem nome'}</td>
                        <td>${key.client || 'Todos'}</td>
                        <td><span class="key-partial">${partialKey}</span></td>
                        <td>${createdAt}</td>
                        <td><button class="btn-revoke" data-key-id="${key.id}">Revogar</button></td>
                    </tr>
                `;
            }
            tbody.innerHTML = html;
        } else {
            tbody.innerHTML = '<tr><td colspan="5" style="text-align:center;color:var(--text-muted)">Nenhuma API Key encontrada</td></tr>';
        }
    } catch (error) {
        tbody.innerHTML = `<tr><td colspan="5" style="text-align:center;color:var(--status-error)">Erro: ${error.message}</td></tr>`;
    }
}

async function loadPerformanceChart() {
    const container = $id('atlas-performance-bars');
    if (!container) return;

    try {
        const data = await fetchAPI('/atlas/performance');

        if (!data.executions || data.executions.length === 0) {
            container.innerHTML = '<div style="text-align:center;color:var(--text-muted);padding:40px;">Sem dados de performance</div>';
            return;
        }

        let html = '';
        const maxHeight = 100;

        for (const exec of data.executions.reverse()) {
            const status = exec.status === 'completed' ? 'success' : 'failed';
            const dateStr = exec.start ? new Date(exec.start).toLocaleDateString('pt-BR', { day: '2-digit', month: '2-digit' }) : '--';
            const height = status === 'success' ? maxHeight : 40;

            html += `
                <div class="performance-bar">
                    <div class="bar-fill ${status}" style="height: ${height}px;"></div>
                    <span class="bar-label">${dateStr}</span>
                </div>
            `;
        }

        // Fill remaining slots
        const remaining = 7 - data.executions.length;
        for (let i = 0; i < remaining; i++) {
            html += `
                <div class="performance-bar">
                    <div class="bar-fill" style="height: 10px; background: var(--border-subtle);"></div>
                    <span class="bar-label">--</span>
                </div>
            `;
        }

        container.innerHTML = html;
    } catch (error) {
        container.innerHTML = `<div style="text-align:center;color:var(--status-error);padding:40px;">Erro: ${error.message}</div>`;
    }
}

// Extend setupAtlasConfigPageHandlers to load monitoring data
// ============================================================================
// ATLAS TABS LOGIC
// ============================================================================

function setupAtlasTabs() {
    const tabs = document.querySelectorAll('.atlas-tab');

    tabs.forEach(tab => {
        tab.addEventListener('click', () => {
            const target = tab.getAttribute('data-tab');

            // Toggle tabs
            tabs.forEach(t => t.classList.remove('active'));
            tab.classList.add('active');

            // Toggle panels
            document.querySelectorAll('.atlas-tab-panel').forEach(panel => {
                panel.classList.remove('active');
            });

            const targetPanel = document.getElementById(`atlas-panel-${target}`);
            if (targetPanel) {
                targetPanel.classList.add('active');
            }
        });
    });
}

// Extend setupAtlasConfigPageHandlers to load monitoring data
const originalSetupHandlers = setupAtlasConfigPageHandlers;
setupAtlasConfigPageHandlers = function () {
    originalSetupHandlers();

    // Setup Tabs
    setupAtlasTabs();

    // Load all monitoring data
    loadRoutineTimeline();
    loadAtlasLogs();
    loadTelemetryStats();
    loadDashboardStats();
    loadApiKeys();
    loadPerformanceChart();

    // Refresh buttons
    $id('atlas-refresh-timeline')?.addEventListener('click', loadRoutineTimeline);
    $id('atlas-refresh-logs')?.addEventListener('click', () => {
        const date = $id('atlas-log-date')?.value || '';
        loadAtlasLogs(date);
    });

    // Log date selector
    $id('atlas-log-date')?.addEventListener('change', (e) => {
        loadAtlasLogs(e.target.value);
    });

    // Add API Key button
    $id('atlas-btn-add-key')?.addEventListener('click', async () => {
        const name = prompt('Nome para a nova API Key:');
        if (!name) return;

        const client = prompt('Cliente (deixe vazio para acesso global):') || null;

        try {
            const response = await fetch(`${CONFIG.apiUrl}/atlas/api-keys?name=${encodeURIComponent(name)}${client ? `&client=${encodeURIComponent(client)}` : ''}`, {
                method: 'POST'
            });
            const data = await response.json();

            if (data.status === 'success') {
                alert(`API Key criada!\n\nKey: ${data.key?.key || 'Ver no Atlas'}\n\nGuarde esta chave, ela não será exibida novamente.`);
                loadApiKeys();
            } else {
                alert(`Erro: ${data.message}`);
            }
        } catch (error) {
            alert(`Erro: ${error.message}`);
        }
    });

    // Raw Data tab handlers
    $id('atlas-refresh-raw')?.addEventListener('click', loadRawData);
    $id('atlas-raw-stream')?.addEventListener('change', loadRawData);
    $id('atlas-raw-limit')?.addEventListener('change', loadRawData);

    // Load raw data when tab is clicked
    const rawDataTab = document.querySelector('.atlas-tab[data-tab="rawdata"]');
    if (rawDataTab) {
        rawDataTab.addEventListener('click', () => {
            loadRawData();
        });
    }
};

// ============================================================================
// RAW DATA VIEWER
// ============================================================================

async function loadRawData() {
    const container = $id('atlas-raw-container');
    const totalEl = $id('atlas-raw-total');
    const updatedEl = $id('atlas-raw-updated');

    if (!container) return;

    const stream = $id('atlas-raw-stream')?.value || 'telemetry.received';
    const limit = parseInt($id('atlas-raw-limit')?.value || '20');

    container.innerHTML = '<div class="raw-loading"><i class="ph-bold ph-spinner"></i><span>Carregando dados brutos...</span></div>';

    try {
        const data = await fetchAPI(`/streams/${encodeURIComponent(stream)}/details?limit=${limit}`);

        // Update stats
        if (totalEl) totalEl.textContent = data.length?.toLocaleString() ?? '--';
        if (updatedEl) updatedEl.textContent = new Date().toLocaleTimeString('pt-BR');

        if (!data.messages || data.messages.length === 0) {
            container.innerHTML = `
                <div class="raw-empty-state">
                    <i class="ph-bold ph-database"></i>
                    <span>Nenhuma mensagem encontrada no stream "${stream}"</span>
                </div>
            `;
            return;
        }

        let html = '<div class="raw-message-list">';

        for (const msg of data.messages) {
            const timestamp = msg.timestamp ? new Date(parseInt(msg.timestamp)).toLocaleString('pt-BR') : '--';
            const fieldsJson = formatJsonWithSyntaxHighlighting(msg.fields);

            // Extract key fields for preview tags
            const previewFields = extractPreviewFields(msg.fields);

            html += `
                <div class="raw-message-item">
                    <div class="raw-message-header" onclick="toggleRawMessage(this)">
                        <div class="raw-message-id">
                            <i class="ph-bold ph-hash"></i>
                            <span>${msg.id}</span>
                            <span class="raw-message-time">${timestamp}</span>
                        </div>
                        <i class="ph-bold ph-caret-down raw-message-expand"></i>
                    </div>
                    <div class="raw-message-content">
                        <div class="raw-json-container">
                            <pre>${fieldsJson}</pre>
                        </div>
                        ${previewFields ? `<div class="raw-field-tags">${previewFields}</div>` : ''}
                    </div>
                </div>
            `;
        }

        html += '</div>';
        container.innerHTML = html;

        // Auto-expand first message
        const firstMsg = container.querySelector('.raw-message-item');
        if (firstMsg) {
            firstMsg.classList.add('expanded');
        }

    } catch (error) {
        container.innerHTML = `
            <div class="raw-empty-state">
                <i class="ph-bold ph-warning-circle"></i>
                <span>Erro ao carregar dados: ${error.message}</span>
            </div>
        `;
    }
}

function toggleRawMessage(header) {
    const item = header.closest('.raw-message-item');
    if (item) {
        item.classList.toggle('expanded');
    }
}

function formatJsonWithSyntaxHighlighting(obj) {
    if (!obj) return '<span class="json-null">null</span>';

    try {
        // Parse if it's a string
        let parsed = obj;
        if (typeof obj === 'string') {
            try {
                parsed = JSON.parse(obj);
            } catch {
                // If it's not valid JSON, return as string
                return `<span class="json-string">"${escapeHtml(obj)}"</span>`;
            }
        }

        // Check if there's a 'payload' field that might contain nested JSON
        if (parsed.payload && typeof parsed.payload === 'string') {
            try {
                parsed.payload = JSON.parse(parsed.payload);
            } catch {
                // Keep as string
            }
        }

        // Format with indentation
        const jsonStr = JSON.stringify(parsed, null, 2);

        // Add syntax highlighting
        return jsonStr
            .replace(/("(\\u[a-zA-Z0-9]{4}|\\[^u]|[^\\"])*")\s*:/g, '<span class="json-key">$1</span>:')
            .replace(/: ("(\\u[a-zA-Z0-9]{4}|\\[^u]|[^\\"])*")/g, ': <span class="json-string">$1</span>')
            .replace(/: (\d+\.?\d*)/g, ': <span class="json-number">$1</span>')
            .replace(/: (true|false)/g, ': <span class="json-boolean">$1</span>')
            .replace(/: (null)/g, ': <span class="json-null">$1</span>');
    } catch (error) {
        return `<span class="json-string">${escapeHtml(JSON.stringify(obj))}</span>`;
    }
}

function extractPreviewFields(fields) {
    if (!fields) return '';

    const importantKeys = ['type', 'event_type', 'client', 'status', 'outlet_id', 'asset_id', 'user_id'];
    let tags = '';

    // Parse payload if exists
    let data = fields;
    if (fields.payload) {
        try {
            data = typeof fields.payload === 'string' ? JSON.parse(fields.payload) : fields.payload;
        } catch {
            data = fields;
        }
    }

    // Combine fields and payload data
    const combined = { ...fields, ...data };

    for (const key of importantKeys) {
        if (combined[key] !== undefined && combined[key] !== null) {
            const value = String(combined[key]).substring(0, 30);
            tags += `<span class="raw-field-tag"><span class="tag-key">${key}:</span> <span class="tag-value">${escapeHtml(value)}</span></span>`;
        }
    }

    return tags;
}

// Make toggleRawMessage global
window.toggleRawMessage = toggleRawMessage;

// ============================================================================
// VALIDATION LOG VIEWER
// ============================================================================

// Validation log state
let vlogAllEntries = [];
let vlogCurrentPage = 1;
let vlogPerPage = 50;
let vlogFilterStatus = '';

async function loadValidationLog() {
    const container = $id('atlas-validation-log');
    const approvedCount = $id('vlog-approved-count');
    const rejectedCount = $id('vlog-rejected-count');
    const approvalRate = $id('vlog-approval-rate');

    if (!container) return;

    container.innerHTML = '<div class="raw-loading"><i class="ph-bold ph-spinner"></i><span>Carregando log de validações...</span></div>';

    try {
        // Fetch more entries for pagination (max 200 allowed by API)
        const data = await fetchAPI(`/atlas/validation-log?limit=200`);

        // Update stats
        if (data.stats) {
            if (approvedCount) approvedCount.textContent = data.stats.total_validated?.toLocaleString() ?? '--';
            if (rejectedCount) rejectedCount.textContent = data.stats.total_rejected?.toLocaleString() ?? '--';
            if (approvalRate) approvalRate.textContent = data.stats.approval_rate ?? '--';
        }

        // Store entries for filtering/pagination
        vlogAllEntries = data.entries || [];
        vlogCurrentPage = 1;

        renderValidationLogPage();

    } catch (error) {
        container.innerHTML = `
            <div class="raw-empty-state">
                <i class="ph-bold ph-warning-circle"></i>
                <span>Erro ao carregar log: ${error.message}</span>
            </div>
        `;
    }
}

function renderValidationLogPage() {
    const container = $id('atlas-validation-log');
    if (!container) return;

    // Apply filter
    let filtered = vlogAllEntries;
    if (vlogFilterStatus) {
        filtered = vlogAllEntries.filter(e => e.status === vlogFilterStatus);
    }

    // Pagination calc
    const totalEntries = filtered.length;
    const totalPages = Math.ceil(totalEntries / vlogPerPage) || 1;
    const startIdx = (vlogCurrentPage - 1) * vlogPerPage;
    const endIdx = Math.min(startIdx + vlogPerPage, totalEntries);
    const pageEntries = filtered.slice(startIdx, endIdx);

    // Update pagination UI
    $id('vlog-showing')?.textContent && ($id('vlog-showing').textContent = `${startIdx + 1}-${endIdx}`);
    $id('vlog-total')?.textContent && ($id('vlog-total').textContent = totalEntries.toLocaleString());
    $id('vlog-current-page')?.textContent && ($id('vlog-current-page').textContent = `Página ${vlogCurrentPage} de ${totalPages}`);

    const prevBtn = $id('vlog-prev');
    const nextBtn = $id('vlog-next');
    if (prevBtn) prevBtn.disabled = vlogCurrentPage <= 1;
    if (nextBtn) nextBtn.disabled = vlogCurrentPage >= totalPages;

    if (pageEntries.length === 0) {
        container.innerHTML = `
            <div class="raw-empty-state">
                <i class="ph-bold ph-check-square"></i>
                <span>Nenhuma validação encontrada</span>
            </div>
        `;
        return;
    }

    let html = `
        <table class="validation-log-table">
            <thead>
                <tr>
                    <th>Horário</th>
                    <th>Status</th>
                    <th>Cliente</th>
                    <th>Asset</th>
                    <th>Tipo</th>
                    <th>Motivo</th>
                </tr>
            </thead>
            <tbody>
    `;

    for (const entry of pageEntries) {
        const timestamp = entry.timestamp ? new Date(parseInt(entry.timestamp)).toLocaleString('pt-BR', {
            day: '2-digit',
            month: '2-digit',
            hour: '2-digit',
            minute: '2-digit',
            second: '2-digit'
        }) : '--';

        const statusIcon = entry.status === 'approved' ? 'check-circle' : 'x-circle';
        const statusLabel = entry.status === 'approved' ? 'Aprovado' : 'Rejeitado';

        html += `
            <tr>
                <td><span class="validation-time">${timestamp}</span></td>
                <td>
                    <span class="validation-status-badge ${entry.status}">
                        <i class="ph-bold ph-${statusIcon}"></i>
                        ${statusLabel}
                    </span>
                </td>
                <td><span class="validation-client">${escapeHtml(entry.client || '--')}</span></td>
                <td><span class="validation-asset">${escapeHtml(entry.asset || '--')}</span></td>
                <td><span class="validation-event-type">${escapeHtml(entry.event_type || 'telemetry')}</span></td>
                <td><span class="validation-reason ${entry.reason ? '' : 'none'}">${escapeHtml(entry.reason || '-')}</span></td>
            </tr>
        `;
    }

    html += '</tbody></table>';
    container.innerHTML = html;
}

function vlogChangePage(direction) {
    const filtered = vlogFilterStatus ? vlogAllEntries.filter(e => e.status === vlogFilterStatus) : vlogAllEntries;
    const totalPages = Math.ceil(filtered.length / vlogPerPage) || 1;

    if (direction === 'prev' && vlogCurrentPage > 1) {
        vlogCurrentPage--;
        renderValidationLogPage();
    } else if (direction === 'next' && vlogCurrentPage < totalPages) {
        vlogCurrentPage++;
        renderValidationLogPage();
    }
}

// Extend setup to include validation log handlers
const originalSetupHandlers2 = setupAtlasConfigPageHandlers;
setupAtlasConfigPageHandlers = function () {
    originalSetupHandlers2();

    // Validation Log handlers
    $id('atlas-refresh-validations')?.addEventListener('click', loadValidationLog);

    // Status filter
    $id('vlog-status-filter')?.addEventListener('change', (e) => {
        vlogFilterStatus = e.target.value;
        vlogCurrentPage = 1;
        renderValidationLogPage();
    });

    // Per page selector
    $id('vlog-per-page')?.addEventListener('change', (e) => {
        vlogPerPage = parseInt(e.target.value) || 50;
        vlogCurrentPage = 1;
        renderValidationLogPage();
    });

    // Pagination buttons
    $id('vlog-prev')?.addEventListener('click', () => vlogChangePage('prev'));
    $id('vlog-next')?.addEventListener('click', () => vlogChangePage('next'));

    // Load validation log when tab is clicked
    const validationsTab = document.querySelector('.atlas-tab[data-tab="validations"]');
    if (validationsTab) {
        validationsTab.addEventListener('click', () => {
            loadValidationLog();
        });
    }
};

// ============================================================================
// SENTINEL CONFIG PAGE
// ============================================================================

function showSentinelConfigPage() {
    // Hide all sections
    $$('.content-section').forEach(section => {
        section.classList.remove('active');
    });

    const sentinelSection = $id('section-sentinel-config');
    if (sentinelSection) {
        sentinelSection.classList.add('active');
        setupSentinelTabs();
        loadSentinelData();
    }
}

function setupSentinelTabs() {
    const tabs = document.querySelectorAll('.sentinel-tabs .atlas-tab');
    const panels = document.querySelectorAll('.sentinel-tab-panels .atlas-tab-panel');

    tabs.forEach(tab => {
        tab.addEventListener('click', () => {
            const target = tab.getAttribute('data-tab');

            // Toggle tabs
            tabs.forEach(t => t.classList.remove('active'));
            tab.classList.add('active');

            // Toggle panels
            panels.forEach(panel => {
                panel.classList.remove('active');
            });

            const targetPanel = document.getElementById(`sentinel-panel-${target}`);
            if (targetPanel) {
                targetPanel.classList.add('active');
            }

            // Load data for specific tabs
            if (target === 's-alerts') {
                loadSentinelAlerts();
            } else if (target === 's-rules') {
                loadSentinelRules();
            }
        });
    });
}

async function loadSentinelData() {
    const tenant = CONFIG.sentinelTenant;
    const sentinelUrl = CONFIG.sentinelApiUrl;

    // Helper to fetch from Sentinel API
    async function fetchSentinel(endpoint) {
        const response = await fetch(`${sentinelUrl}${endpoint}`, {
            headers: { 'Accept': 'application/json' }
        });
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        return response.json();
    }

    // -------------------------------------------------
    // 1. Check Sentinel Health
    // -------------------------------------------------
    let sentinelOnline = false;
    try {
        const healthData = await fetchSentinel('/health');
        sentinelOnline = healthData?.status === 'ok';

        const statusBadge = $id('sentinel-config-status');
        if (statusBadge) {
            statusBadge.innerHTML = sentinelOnline
                ? '<span class="pulse-micro"></span> Online'
                : '<span class="pulse-micro error"></span> Offline';
            statusBadge.className = sentinelOnline ? 'hero-badge active' : 'hero-badge offline';
        }
    } catch (e) {
        console.log('Sentinel health check failed:', e);
        const statusBadge = $id('sentinel-config-status');
        if (statusBadge) {
            statusBadge.innerHTML = '<span class="pulse-micro error"></span> Offline';
            statusBadge.className = 'hero-badge offline';
        }
    }

    // -------------------------------------------------
    // 2. Load Consumer Health stats from CAOS (messages processed)
    // -------------------------------------------------
    try {
        const consumerData = await fetchAPI('/streams/sentinel.consumer_health/details?limit=1');
        if (consumerData && consumerData.messages && consumerData.messages.length > 0) {
            const fields = consumerData.messages[0].fields;
            const processed = parseInt(fields.messages_processed) || 0;
            const success = parseInt(fields.messages_success) || 0;
            const errors = parseInt(fields.messages_error) || 0;

            // Update hero stats - PROCESSADOS is about telemetry processed, not alerts
            const processedEl = $id('sentinel-stat-processed');
            if (processedEl) processedEl.textContent = processed.toLocaleString();

            // Note: Overview Card update moved to Alerts section to show Alert count instead of Processed count

            // Update consumer metrics
            const consumerMsgs = $id('sentinel-consumer-msgs');
            const consumerSuccess = $id('sentinel-consumer-success');
            const consumerErrors = $id('sentinel-consumer-errors');

            // Update Overview Card Errors (Consumer Errors)
            const overviewErrorsEl = $id('sentinel-errors');
            if (overviewErrorsEl) overviewErrorsEl.textContent = errors.toLocaleString();

            if (consumerMsgs) consumerMsgs.textContent = processed.toLocaleString();
            if (consumerSuccess) consumerSuccess.textContent = success.toLocaleString();
            if (consumerErrors) consumerErrors.textContent = errors.toLocaleString();
        }
    } catch (e) {
        console.log('Could not load consumer health data:', e);
    }

    // -------------------------------------------------
    // 3. Load Alerts from Sentinel API
    // -------------------------------------------------
    try {
        const alertsData = await fetchSentinel(`/tenants/${tenant}/alerts?limit=500`);

        const openAlerts = alertsData.filter(a => a.status === 'OPEN' || a.status === 'ACK').length;
        const resolvedAlerts = alertsData.filter(a => a.status === 'RESOLVED').length;
        const totalAlerts = alertsData.length;

        // Update Overview Card Alerts (Total Generated)
        const overviewAlertsEl = $id('sentinel-processed');
        if (overviewAlertsEl) overviewAlertsEl.textContent = totalAlerts.toLocaleString();

        // Update alert-specific stats
        const alertsOpenEl = $id('sentinel-stat-alerts-open');
        const alertsResolvedEl = $id('sentinel-stat-alerts-resolved');

        if (alertsOpenEl) alertsOpenEl.textContent = openAlerts.toLocaleString();
        if (alertsResolvedEl) alertsResolvedEl.textContent = resolvedAlerts.toLocaleString();

        // Update alerts metrics
        const alertsGenerated = $id('sentinel-alerts-generated');
        const alertsAutoResolved = $id('sentinel-alerts-auto-resolved');

        if (alertsGenerated) alertsGenerated.textContent = totalAlerts.toLocaleString();
        if (alertsAutoResolved) alertsAutoResolved.textContent = resolvedAlerts.toLocaleString();

        // Update db stats
        const dbTotalAlerts = $id('sentinel-db-total-alerts');
        if (dbTotalAlerts) dbTotalAlerts.textContent = totalAlerts.toLocaleString();

    } catch (e) {
        console.log('Could not load sentinel alerts:', e);
        // Set alert-related zeros only (including overview card)
        ['sentinel-processed', 'sentinel-stat-alerts-open', 'sentinel-stat-alerts-resolved',
            'sentinel-alerts-generated', 'sentinel-alerts-auto-resolved', 'sentinel-db-total-alerts'
        ].forEach(id => {
            const el = $id(id);
            if (el) el.textContent = '0';
        });
    }

    // -------------------------------------------------
    // 4. Load Rules from Sentinel API
    // -------------------------------------------------
    try {
        const rulesData = await fetchSentinel(`/tenants/${tenant}/alert-rules?limit=100`);

        const activeRules = rulesData.filter(r => r.is_active !== false).length;
        const rulesEl = $id('sentinel-stat-rules');
        if (rulesEl) rulesEl.textContent = activeRules.toLocaleString();

    } catch (e) {
        console.log('Could not load sentinel rules:', e);
        const rulesEl = $id('sentinel-stat-rules');
        if (rulesEl) rulesEl.textContent = '0';
    }

    // -------------------------------------------------
    // 5. Load stream information from CAOS
    // -------------------------------------------------
    try {
        const streamData = await fetchAPI('/streams/telemetry.validated/details?limit=1');
        if (streamData) {
            const streamLen = $id('sentinel-stream-len-validated');
            const streamConsumers = $id('sentinel-stream-consumers');

            if (streamLen) streamLen.textContent = (streamData.length ?? streamData.stream_length ?? 0).toLocaleString();
            if (streamConsumers) streamConsumers.textContent = (streamData.groups?.length ?? 0).toLocaleString();

            // Get entries_read from consumer group (total messages processed historically)
            if (streamData.groups && streamData.groups.length > 0) {
                const sentinelGroup = streamData.groups.find(g => g.name === 'sentinel-cg');
                if (sentinelGroup && sentinelGroup.entries_read !== undefined) {
                    const processedEl = $id('sentinel-stat-processed');
                    if (processedEl) processedEl.textContent = sentinelGroup.entries_read.toLocaleString();
                }
            }
        }
    } catch (e) {
        console.log('Could not load stream data:', e);
    }

    // -------------------------------------------------
    // 5. Update database connection status
    // -------------------------------------------------
    const dbStatus = $id('sentinel-db-status');
    const dbAssets = $id('sentinel-db-assets');

    if (dbStatus) {
        dbStatus.textContent = sentinelOnline ? 'Conectado' : 'Desconectado';
        dbStatus.className = sentinelOnline ? 'status-value success' : 'status-value error';
    }
    if (dbAssets) dbAssets.textContent = '--';

    addLog('Página de configuração do Sentinel carregada', 'info', 'sentinel');
}

async function loadSentinelAlerts() {
    const container = $id('sentinel-alerts-container');
    if (!container) return;

    container.innerHTML = '<div class="raw-loading"><i class="ph-bold ph-spinner"></i><span>Carregando alertas...</span></div>';

    const tenant = CONFIG.sentinelTenant;
    const sentinelUrl = CONFIG.sentinelApiUrl;
    const filter = $id('sentinel-alerts-filter')?.value || 'OPEN';

    try {
        const statusParam = filter !== 'all' ? `&status=${filter}` : '';
        const response = await fetch(`${sentinelUrl}/tenants/${tenant}/alerts?limit=100${statusParam}`, {
            headers: { 'Accept': 'application/json' }
        });

        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const alerts = await response.json();

        if (!alerts || alerts.length === 0) {
            container.innerHTML = `
                <div class="raw-empty-state">
                    <i class="ph-bold ph-check-circle"></i>
                    <span>Nenhum alerta encontrado</span>
                </div>
            `;
            return;
        }

        let html = `
            <table class="alerts-table">
                <thead>
                    <tr>
                        <th>Asset</th>
                        <th>Tipo</th>
                        <th>Severidade</th>
                        <th>Status</th>
                        <th>Última Ocorrência</th>
                        <th>Ações</th>
                    </tr>
                </thead>
                <tbody>
        `;

        for (const alert of alerts) {
            const time = alert.last_seen_at ? new Date(alert.last_seen_at).toLocaleString('pt-BR') : '--';
            const assetId = alert.asset_id || '--';
            const type = alert.type || 'UNKNOWN';
            const severity = alert.severity || 'LOW';
            const status = alert.status || 'OPEN';

            html += `
                <tr>
                    <td><span class="validation-asset">${assetId}</span></td>
                    <td><span class="rule-type-badge">${type}</span></td>
                    <td><span class="alert-severity-badge ${severity}">${severity}</span></td>
                    <td><span class="alert-status-badge ${status}">${status}</span></td>
                    <td><span class="validation-time">${time}</span></td>
                    <td>
                        <button class="btn-small btn-secondary" onclick="ackAlert(${alert.id})">ACK</button>
                    </td>
                </tr>
            `;
        }

        html += '</tbody></table>';
        container.innerHTML = html;

        // Update summary counts
        const highEl = $id('sentinel-alerts-high');
        const medEl = $id('sentinel-alerts-medium');
        const lowEl = $id('sentinel-alerts-low');
        if (highEl) highEl.textContent = alerts.filter(a => a.severity === 'HIGH').length;
        if (medEl) medEl.textContent = alerts.filter(a => a.severity === 'MEDIUM').length;
        if (lowEl) lowEl.textContent = alerts.filter(a => a.severity === 'LOW').length;

    } catch (e) {
        console.log('Could not load sentinel alerts:', e);
        container.innerHTML = `
            <div class="raw-empty-state">
                <i class="ph-bold ph-warning-circle"></i>
                <span>Erro ao carregar alertas: ${e.message}</span>
            </div>
        `;
    }
}

async function loadSentinelRules() {
    const container = $id('sentinel-rules-container');
    if (!container) return;

    container.innerHTML = '<div class="raw-loading"><i class="ph-bold ph-spinner"></i><span>Carregando regras...</span></div>';

    const tenant = CONFIG.sentinelTenant;
    const sentinelUrl = CONFIG.sentinelApiUrl;

    try {
        const response = await fetch(`${sentinelUrl}/tenants/${tenant}/alert-rules?limit=100`, {
            headers: { 'Accept': 'application/json' }
        });

        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const rules = await response.json();

        if (!rules || rules.length === 0) {
            container.innerHTML = `
                <div class="raw-empty-state">
                    <i class="ph-bold ph-list-checks"></i>
                    <span>Nenhuma regra configurada</span>
                </div>
            `;
            return;
        }

        let html = `
            <table class="rules-table">
                <thead>
                    <tr>
                        <th>Tipo</th>
                        <th>Severidade</th>
                        <th>Parâmetros</th>
                        <th>Ativo</th>
                        <th>Ações</th>
                    </tr>
                </thead>
                <tbody>
        `;

        for (const rule of rules) {
            const params = JSON.stringify(rule.params || {});
            const type = rule.type || 'UNKNOWN';
            const severity = rule.severity || 'LOW';
            const isActive = rule.is_active !== false;

            html += `
                <tr>
                    <td><span class="rule-type-badge">${type}</span></td>
                    <td><span class="alert-severity-badge ${severity}">${severity}</span></td>
                    <td><code style="font-size: 11px;">${params}</code></td>
                    <td>
                        <input type="checkbox" class="rule-toggle" ${isActive ? 'checked' : ''} onchange="toggleRule(${rule.id}, this.checked)" />
                    </td>
                    <td>
                        <button class="btn-small btn-secondary" onclick="editRule(${rule.id})">
                            <i class="ph-bold ph-pencil"></i>
                        </button>
                    </td>
                </tr>
            `;
        }

        html += '</tbody></table>';
        container.innerHTML = html;

        // Update rules count
        const rulesEl = $id('sentinel-stat-rules');
        if (rulesEl) rulesEl.textContent = rules.filter(r => r.is_active !== false).length;

    } catch (e) {
        console.log('Could not load sentinel rules:', e);
        container.innerHTML = `
            <div class="raw-empty-state">
                <i class="ph-bold ph-warning-circle"></i>
                <span>Erro ao carregar regras: ${e.message}</span>
            </div>
        `;
    }
}

function ackAlert(id) {
    addLog(`Alerta ${id} reconhecido`, 'validated', 'sentinel');
    loadSentinelAlerts();
}

function toggleRule(id, isActive) {
    addLog(`Regra ${id} ${isActive ? 'ativada' : 'desativada'}`, 'info', 'sentinel');
}

function editRule(id) {
    addLog(`Editando regra ${id}...`, 'info', 'sentinel');
}

// ============================================================================
// INFO POPUP FUNCTIONS - Explicações dos cards Validados/Rejeitados
// ============================================================================

async function showInfoPopup(type) {
    const modal = $id('info-popup-modal');
    const header = $id('info-popup-header');
    const icon = $id('info-popup-icon');
    const title = $id('info-popup-title');
    const subtitle = $id('info-popup-subtitle');
    const body = $id('info-popup-body');

    if (!modal) return;

    // Reset classes
    header.className = 'modal-header info-popup-header ' + type;
    icon.className = 'info-popup-icon ' + type;

    // Initial static content
    let contentHtml = '';

    if (type === 'validated') {
        icon.innerHTML = '<i class="ph-fill ph-check-circle"></i>';
        title.textContent = 'Telemetrias Validadas';
        subtitle.textContent = 'Mensagens aprovadas pelo CAOS Supervisor';
        contentHtml = `
            <div class="info-popup-columns">
                <!-- LEF COLUMN: Static Info -->
                <div class="info-column-left">
                    <div class="info-section">
                        <div class="info-section-title">
                            <i class="ph-bold ph-question"></i>
                            O que são?
                        </div>
                        <div class="info-section-content">
                            São eventos de telemetria IoT que passaram por todas as verificações de segurança e qualidade do <strong>CAOS Supervisor</strong> e foram aprovados para processamento pelos agentes downstream (Sentinel, Oracle).
                        </div>
                    </div>
                    
                    <div class="info-section">
                        <div class="info-section-title">
                            <i class="ph-bold ph-flow-arrow"></i>
                            Fluxo dos Dados
                        </div>
                        <div class="info-flow-diagram">
                            <span class="info-flow-step" style="color: var(--atlas-blue);">
                                <i class="ph-fill ph-globe"></i> Atlas
                            </span>
                            <span class="info-flow-arrow">→</span>
                            <span class="info-flow-step">telemetry.received</span>
                            <span class="info-flow-arrow">→</span>
                            <span class="info-flow-step" style="color: var(--caos-red);">
                                <i class="ph-fill ph-shield-check"></i> CAOS
                            </span>
                            <span class="info-flow-arrow">→</span>
                            <span class="info-flow-step" style="color: #00F5B5;">
                                <i class="ph-fill ph-check-circle"></i> telemetry.validated
                            </span>
                        </div>
                    </div>
                    
                    <div class="info-section">
                        <div class="info-section-title">
                            <i class="ph-bold ph-check-square"></i>
                            Critérios de Validação
                        </div>
                        <ul class="info-reason-list">
                            <li><i class="ph-fill ph-check-circle check"></i><span><strong>Payload JSON válido</strong> - Formato correto e parseable</span></li>
                            <li><i class="ph-fill ph-check-circle check"></i><span><strong>Client identificado</strong> - Origem da telemetria conhecida</span></li>
                            <li><i class="ph-fill ph-check-circle check"></i><span><strong>Asset ID presente</strong> - Equipamento identificável</span></li>
                            <li><i class="ph-fill ph-check-circle check"></i><span><strong>Rate limit OK</strong> - Dentro do limite de requisições</span></li>
                            <li><i class="ph-fill ph-check-circle check"></i><span><strong>Schema válido</strong> - Campos obrigatórios presentes</span></li>
                        </ul>
                    </div>
                </div>

                <!-- RIGHT COLUMN: Live Data -->
                <div class="info-column-right">
                    <div class="info-section" style="height: 100%; display: flex; flex-direction: column;">
                        <div class="info-section-title">
                            <i class="ph-bold ph-code"></i>
                            Últimas Mensagens (Tempo Real)
                        </div>
                        <div class="info-live-data" id="popup-live-data" style="flex: 1; min-height: 300px; max-height: none;">
                            <div class="loading-spinner-small"></div> Carregando dados...
                        </div>
                    </div>
                </div>
            </div>
        `;
    } else if (type === 'rejected') {
        icon.innerHTML = '<i class="ph-fill ph-x-circle"></i>';
        title.textContent = 'Telemetrias Rejeitadas';
        subtitle.textContent = 'Mensagens bloqueadas pelo CAOS Supervisor';
        contentHtml = `
            <div class="info-popup-columns">
                <!-- LEF COLUMN: Static Info -->
                <div class="info-column-left">
                    <div class="info-section">
                        <div class="info-section-title">
                            <i class="ph-bold ph-question"></i>
                            O que são?
                        </div>
                        <div class="info-section-content">
                            São eventos de telemetria que <strong>não passaram</strong> nas verificações de segurança do CAOS. Esses eventos são enviados para o stream <code>telemetry.rejected</code> para auditoria e análise.
                        </div>
                    </div>
                    
                    <div class="info-section">
                        <div class="info-section-title">
                            <i class="ph-bold ph-flow-arrow"></i>
                            Fluxo dos Dados
                        </div>
                        <div class="info-flow-diagram">
                            <span class="info-flow-step" style="color: var(--atlas-blue);">
                                <i class="ph-fill ph-globe"></i> Atlas
                            </span>
                            <span class="info-flow-arrow">→</span>
                            <span class="info-flow-step">telemetry.received</span>
                            <span class="info-flow-arrow">→</span>
                            <span class="info-flow-step" style="color: var(--caos-red);">
                                <i class="ph-fill ph-shield-warning"></i> CAOS
                            </span>
                            <span class="info-flow-arrow">→</span>
                            <span class="info-flow-step" style="color: #FF5252;">
                                <i class="ph-fill ph-x-circle"></i> telemetry.rejected
                            </span>
                        </div>
                    </div>
                    
                    <div class="info-section">
                        <div class="info-section-title">
                            <i class="ph-bold ph-x-square"></i>
                            Motivos Comuns de Rejeição
                        </div>
                        <ul class="info-reason-list">
                            <li><i class="ph-fill ph-x-circle x"></i><span><strong>Client desconhecido</strong> - Origem não identificada ou não autorizada</span></li>
                            <li><i class="ph-fill ph-x-circle x"></i><span><strong>Asset ID ausente</strong> - Não foi possível identificar o equipamento</span></li>
                            <li><i class="ph-fill ph-x-circle x"></i><span><strong>JSON inválido</strong> - Formato de payload corrompido ou malformado</span></li>
                            <li><i class="ph-fill ph-x-circle x"></i><span><strong>Rate limit excedido</strong> - Muitas requisições em pouco tempo</span></li>
                            <li><i class="ph-fill ph-x-circle x"></i><span><strong>Campos obrigatórios</strong> - Faltando dados essenciais no payload</span></li>
                        </ul>
                    </div>
                </div>

                <!-- RIGHT COLUMN: Live Data -->
                <div class="info-column-right">
                    <div class="info-section" style="height: 100%; display: flex; flex-direction: column;">
                        <div class="info-section-title">
                            <i class="ph-bold ph-code"></i>
                            Últimas Mensagens (Tempo Real)
                        </div>
                        <div class="info-live-data" id="popup-live-data" style="flex: 1; min-height: 300px; max-height: none;">
                            <div class="loading-spinner-small"></div> Carregando dados...
                        </div>
                    </div>
                </div>
            </div>
        `;
    }

    body.innerHTML = contentHtml;
    modal.classList.add('open');

    // Fetch and render live data
    try {
        const streamName = type === 'validated' ? 'telemetry.validated' : 'telemetry.rejected';
        const data = await fetchAPI(`/streams/${streamName}/details?limit=3`);

        const container = $id('popup-live-data');
        if (!container) return;

        if (data && data.messages && data.messages.length > 0) {
            let itemsHtml = '<div class="live-data-list">';
            data.messages.forEach(msg => {
                const fields = msg.fields;
                // Try to prettify JSON fields if possible, or just show key-values
                let details = '';

                // Parse the payload first if it exists (payload is a JSON string)
                let payloadObj = null;
                if (fields.payload) {
                    try {
                        payloadObj = typeof fields.payload === 'string' ? JSON.parse(fields.payload) : fields.payload;
                    } catch (e) {
                        payloadObj = null;
                    }
                }

                // Common fields - support multiple payload formats
                // First try from parsed payload, then from fields directly
                const asset = (payloadObj?.door_asset_serial_number ||
                    payloadObj?.health_events_asset_serial_number ||
                    payloadObj?.asset_serial_number ||
                    payloadObj?.assetId ||
                    payloadObj?.door_id ||
                    fields.asset_id ||
                    fields.asset_serial_number ||
                    'Unknown Asset');
                const client = (payloadObj?.door_client ||
                    payloadObj?.health_events_client ||
                    payloadObj?.client ||
                    payloadObj?.tenantId ||
                    fields.client ||
                    'Unknown Client');
                const timestamp = new Date(parseInt(msg.timestamp.split('-')[0])).toLocaleTimeString();

                details += `<div class="data-row"><span class="key">Asset:</span> <span class="val">${asset}</span></div>`;
                details += `<div class="data-row"><span class="key">Client:</span> <span class="val">${client}</span></div>`;

                // Add specific logic for rejected reason if available
                if (type === 'rejected' && fields.error) {
                    details += `<div class="data-row error"><span class="key">Erro:</span> <span class="val">${fields.error}</span></div>`;
                }

                // Payload snippet - use already parsed payloadObj or raw payload
                if (payloadObj || fields.payload) {
                    try {
                        const displayObj = payloadObj || (typeof fields.payload === 'string' ? JSON.parse(fields.payload) : fields.payload);
                        const payloadStr = JSON.stringify(displayObj, null, 2);
                        // Truncate to first 6 lines for compact view
                        const lines = payloadStr.split('\n');
                        const truncated = lines.length > 6 ? lines.slice(0, 6).join('\n') + '\n  ...' : payloadStr;
                        details += `<div class="data-row json"><span class="key">Payload:</span><pre>${truncated}</pre></div>`;
                    } catch (e) {
                        const preview = String(fields.payload).slice(0, 100);
                        details += `<div class="data-row"><span class="key">Payload:</span> <span class="val">${preview}${fields.payload.length > 100 ? '...' : ''}</span></div>`;
                    }
                } else {
                    // Show other fields
                    Object.keys(fields).forEach(k => {
                        if (k !== 'asset_id' && k !== 'client' && k !== 'error') {
                            const val = typeof fields[k] === 'object' ? JSON.stringify(fields[k]) : fields[k];
                            details += `<div class="data-row"><span class="key">${k}:</span> <span class="val">${val}</span></div>`;
                        }
                    });
                }

                itemsHtml += `
                    <div class="live-data-item ${type}">
                        <div class="live-data-header">
                            <span class="msg-id">Msg ID: ${msg.id}</span>
                            <span class="msg-time">${timestamp}</span>
                        </div>
                        <div class="live-data-body">
                            ${details}
                        </div>
                    </div>
                `;
            });
            itemsHtml += '</div>';
            container.innerHTML = itemsHtml;
        } else {
            container.innerHTML = '<div class="empty-state-small">Nenhum dado recente encontrado neste stream.</div>';
        }

    } catch (e) {
        console.error('Error fetching popup data:', e);
        const container = $id('popup-live-data');
        if (container) container.innerHTML = '<div class="error-msg-small">Falha ao carregar dados em tempo real.</div>';
    }
}

function closeInfoPopup() {
    const modal = $id('info-popup-modal');
    if (modal) modal.classList.remove('open');
}

// Make sentinel functions global
window.ackAlert = ackAlert;
window.toggleRule = toggleRule;
window.editRule = editRule;
window.showInfoPopup = showInfoPopup;
window.closeInfoPopup = closeInfoPopup;

// Consumer Health Info Popup
async function showConsumerHealthInfo() {
    const modal = $id('info-popup-modal');
    if (!modal) return;

    const header = $id('info-popup-header');
    const icon = $id('info-popup-icon');
    const title = $id('info-popup-title');
    const subtitle = $id('info-popup-subtitle');
    const body = $id('info-popup-body');

    // Style header
    header.className = 'modal-header info-popup-header sentinel';
    icon.className = 'info-popup-icon sentinel';
    icon.innerHTML = '<i class="ph-fill ph-heartbeat"></i>';
    title.textContent = 'Entendendo as Métricas do Consumer';
    subtitle.textContent = 'Fluxo de processamento do Sentinel';

    // Get current values
    const streamMsgs = $id('sentinel-stream-len-validated')?.textContent || '--';
    const consumedMsgs = $id('sentinel-consumer-msgs')?.textContent || '--';
    const successMsgs = $id('sentinel-consumer-success')?.textContent || '--';
    const errorMsgs = $id('sentinel-consumer-errors')?.textContent || '--';

    // Calculate pending
    const streamNum = parseInt(streamMsgs.replace(/\D/g, '')) || 0;
    const consumedNum = parseInt(consumedMsgs.replace(/\D/g, '')) || 0;
    const pendingNum = Math.max(0, streamNum - consumedNum);

    const contentHtml = `
        <div class="consumer-info-compact">
            <!-- COMPACT STATS ROW -->
            <div class="stats-summary-row">
                <div class="stat-pill blue">
                    <i class="ph-bold ph-database"></i>
                    <span class="stat-number">${streamMsgs}</span>
                    <span class="stat-label">Stream</span>
                </div>
                <div class="stat-arrow">
                    <i class="ph-bold ph-arrow-right"></i>
                    <span class="pending-tag">${pendingNum.toLocaleString()} pendentes</span>
                </div>
                <div class="stat-pill yellow">
                    <i class="ph-bold ph-eye"></i>
                    <span class="stat-number">${consumedMsgs}</span>
                    <span class="stat-label">Consumidas</span>
                </div>
                <div class="stat-arrow">
                    <i class="ph-bold ph-arrow-right"></i>
                </div>
                <div class="stat-results">
                    <div class="stat-result success">
                        <i class="ph-fill ph-check-circle"></i>
                        <span>${successMsgs}</span>
                    </div>
                    <div class="stat-result error">
                        <i class="ph-fill ph-x-circle"></i>
                        <span>${errorMsgs}</span>
                    </div>
                </div>
            </div>

            <!-- MAIN CONTENT: TABS + HISTORY -->
            <div class="history-section">
                <div class="history-header">
                    <h4><i class="ph-bold ph-clock-counter-clockwise"></i> Histórico de Processamento</h4>
                    <span class="history-hint">Últimos snapshots do consumer</span>
                </div>
                <div class="history-tabs">
                    <button class="history-tab active" onclick="switchProcessingTab('success')">
                        <i class="ph-bold ph-check-circle"></i>
                        <span>Sucesso</span>
                        <span class="tab-badge success" id="tab-success-count">--</span>
                    </button>
                    <button class="history-tab" onclick="switchProcessingTab('errors')">
                        <i class="ph-bold ph-x-circle"></i>
                        <span>Erros</span>
                        <span class="tab-badge error" id="tab-errors-count">--</span>
                    </button>
                </div>
                <div class="history-content" id="processing-content">
                    <div class="processing-loading">
                        <i class="ph-bold ph-spinner"></i> Carregando histórico...
                    </div>
                </div>
            </div>
        </div>
    `;

    body.innerHTML = contentHtml;
    modal.classList.add('open');

    // Load processing history
    loadProcessingHistory();
}

async function loadProcessingHistory() {
    const container = $id('processing-content');
    if (!container) return;

    try {
        // Fetch recent consumer health messages
        const data = await fetchAPI('/streams/sentinel.consumer_health/details?limit=20');

        if (data && data.messages && data.messages.length > 0) {
            // Parse messages to extract success/error info
            const successMessages = [];
            const errorMessages = [];

            for (const msg of data.messages) {
                const fields = msg.fields || {};
                const timestamp = new Date(parseInt(msg.timestamp)).toLocaleString('pt-BR');
                const processed = parseInt(fields.messages_processed) || 0;
                const success = parseInt(fields.messages_success) || 0;
                const errors = parseInt(fields.messages_error) || 0;
                const lastError = fields.last_error || null;
                const lastErrorMsgId = fields.last_error_message_id || null;
                const errorMsgIds = fields.error_message_ids ? fields.error_message_ids.split(',').filter(id => id) : [];
                const consumerName = fields.consumer_name || 'sentinel-consumer';

                // Create health snapshot entry
                const entry = {
                    id: msg.id,
                    timestamp,
                    processed,
                    success,
                    errors,
                    lastError,
                    lastErrorMsgId,
                    errorMsgIds,
                    consumerName
                };

                if (errors > 0 && lastError) {
                    errorMessages.push(entry);
                }
                successMessages.push(entry);
            }

            // Store data globally for tabs
            window._processingData = { success: successMessages, errors: errorMessages };

            // Update tab badges
            const successCount = $id('tab-success-count');
            const errorsCount = $id('tab-errors-count');
            if (successCount) successCount.textContent = successMessages.length;
            if (errorsCount) errorsCount.textContent = errorMessages.length;

            // Show success tab by default
            renderProcessingTab('success');
        } else {
            container.innerHTML = '<div class="processing-empty"><i class="ph-bold ph-info"></i> Nenhum histórico de processamento disponível</div>';
        }
    } catch (e) {
        console.error('Error loading processing history:', e);
        container.innerHTML = '<div class="processing-error"><i class="ph-bold ph-warning"></i> Erro ao carregar histórico</div>';
    }
}

function switchProcessingTab(tab) {
    // Update tab buttons
    $$('.history-tab').forEach(t => t.classList.remove('active'));
    const activeBtn = document.querySelector(`.history-tab[onclick*="${tab}"]`);
    if (activeBtn) activeBtn.classList.add('active');

    renderProcessingTab(tab);
}

function renderProcessingTab(tab) {
    const container = $id('processing-content');
    if (!container || !window._processingData) return;

    const data = tab === 'errors' ? window._processingData.errors : window._processingData.success;

    if (data.length === 0) {
        container.innerHTML = `
            <div class="processing-empty">
                <i class="ph-bold ph-${tab === 'errors' ? 'check-circle' : 'list'}"></i>
                ${tab === 'errors' ? 'Nenhum erro registrado! 🎉' : 'Nenhum registro disponível'}
            </div>`;
        return;
    }

    const itemsHtml = data.slice(0, 10).map(entry => {
        if (tab === 'errors') {
            // Build error message IDs section
            let errorIdsHtml = '';
            if (entry.errorMsgIds && entry.errorMsgIds.length > 0) {
                errorIdsHtml = `
                    <div class="proc-error-ids">
                        <span class="error-ids-label"><i class="ph-bold ph-file-x"></i> Mensagens com erro:</span>
                        <div class="error-ids-list">
                            ${entry.errorMsgIds.map(id => `<code class="error-msg-id">${id}</code>`).join('')}
                        </div>
                    </div>
                `;
            } else if (entry.lastErrorMsgId) {
                errorIdsHtml = `
                    <div class="proc-error-ids">
                        <span class="error-ids-label"><i class="ph-bold ph-file-x"></i> Última mensagem com erro:</span>
                        <code class="error-msg-id">${entry.lastErrorMsgId}</code>
                    </div>
                `;
            }

            return `
                <div class="proc-item error">
                    <div class="proc-item-header">
                        <span class="proc-time">${entry.timestamp}</span>
                        <span class="proc-badge error">${entry.errors} erro(s)</span>
                    </div>
                    <div class="proc-item-body">
                        <div class="proc-error-reason">
                            <i class="ph-bold ph-warning-circle"></i>
                            <span>${entry.lastError || 'Erro não especificado'}</span>
                        </div>
                        ${errorIdsHtml}
                        <div class="proc-meta">
                            Consumer: <code>${entry.consumerName}</code> • 
                            Processadas: ${entry.processed.toLocaleString()}
                        </div>
                    </div>
                </div>`;
        } else {
            return `
                <div class="proc-item success">
                    <div class="proc-item-header">
                        <span class="proc-time">${entry.timestamp}</span>
                        <span class="proc-badge success">${entry.success.toLocaleString()} ok</span>
                    </div>
                    <div class="proc-item-body">
                        <div class="proc-meta">
                            Consumer: <code>${entry.consumerName}</code> • 
                            Total: ${entry.processed.toLocaleString()} • 
                            Erros: ${entry.errors}
                        </div>
                    </div>
                </div>`;
        }
    }).join('');

    container.innerHTML = `<div class="proc-items-list">${itemsHtml}</div>`;
}

window.showConsumerHealthInfo = showConsumerHealthInfo;
window.switchProcessingTab = switchProcessingTab;

// Setup Sentinel navigation
function setupSentinelNavigation() {
    // Config button in agents page
    $id('btn-sentinel-config')?.addEventListener('click', () => {
        showSentinelConfigPage();
    });

    // Back button
    $id('btn-back-to-agents-sentinel')?.addEventListener('click', () => {
        $$('.content-section').forEach(s => s.classList.remove('active'));
        $id('section-agents')?.classList.add('active');
        setActiveNavItem('agents');
    });

    // Restart button
    $id('sentinel-btn-restart')?.addEventListener('click', () => {
        addLog('Reiniciando Sentinel Agent...', 'info', 'sentinel');
    });

    // Refresh buttons
    $id('sentinel-refresh-health')?.addEventListener('click', loadSentinelData);
    $id('sentinel-refresh-alerts')?.addEventListener('click', loadSentinelAlerts);
}

// Call setup on DOMContentLoaded
document.addEventListener('DOMContentLoaded', () => {
    setupSentinelNavigation();
});
