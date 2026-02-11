/**
 * CAOS Control Center - JavaScript
 * Real-time monitoring and API communication
 */

// ============================================================================
// CONFIGURATION
// ============================================================================
const CONFIG = {
    apiUrl: localStorage.getItem('caos_api_url') || 'http://localhost:8001',
    pollInterval: 3000,
    maxLogEntries: 50,
};

// ============================================================================
// STATE
// ============================================================================
let isConnected = false;
let pollTimer = null;
let auditLog = [];

// ============================================================================
// DOM ELEMENTS
// ============================================================================
const $ = (id) => document.getElementById(id);

const elements = {
    // Connection
    connectionStatus: $('connection-status'),
    clock: $('clock'),
    apiUrlDisplay: $('api-url-display'),
    apiUrlInput: $('api-url-input'),
    apiConnect: $('api-connect'),

    // Stats
    healthValue: $('health-value'),
    healthIndicator: $('health-indicator'),
    uptimeValue: $('uptime-value'),
    cbSummary: $('cb-summary'),
    cbIndicator: $('cb-indicator'),

    // Stream
    filterStatus: $('filter-status'),
    approvedCount: $('approved-count'),
    rejectedCount: $('rejected-count'),

    // Circuit Breakers
    cbGrid: $('cb-grid'),
    refreshCb: $('refresh-cb'),

    // Rules
    rulesBody: $('rules-body'),

    // Audit Log
    auditLogEl: $('audit-log'),

    // Monitors
    routineStatus: $('routine-status'),
    syncStatus: $('sync-status'),
    consumerStatus: $('consumer-status'),
};

// ============================================================================
// UTILITIES
// ============================================================================
function formatUptime(seconds) {
    const h = Math.floor(seconds / 3600);
    const m = Math.floor((seconds % 3600) / 60);
    const s = Math.floor(seconds % 60);
    return `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
}

function updateClock() {
    const now = new Date();
    if (elements.clock) {
        elements.clock.textContent = now.toLocaleTimeString('pt-BR', { hour12: false });
    }
}

function setConnectionStatus(connected, message = '') {
    isConnected = connected;

    if (elements.connectionStatus) {
        elements.connectionStatus.className = 'connection-status ' + (connected ? 'online' : 'offline');
        elements.connectionStatus.querySelector('.status-label').textContent =
            message || (connected ? 'ONLINE' : 'OFFLINE');
    }
}

function addLogEntry(message, type = 'info') {
    const now = new Date();
    const time = now.toLocaleTimeString('pt-BR', { hour12: false });

    auditLog.unshift({ time, message, type });
    if (auditLog.length > CONFIG.maxLogEntries) {
        auditLog.pop();
    }

    renderAuditLog();
}

function renderAuditLog() {
    if (!elements.auditLogEl) return;

    elements.auditLogEl.innerHTML = auditLog.map(entry => `
        <div class="log-entry ${entry.type}">
            <span class="log-time">${entry.time}</span>
            <span class="log-msg">${entry.message}</span>
        </div>
    `).join('');
}

// ============================================================================
// API FUNCTIONS
// ============================================================================
async function fetchAPI(endpoint) {
    try {
        const response = await fetch(`${CONFIG.apiUrl}${endpoint}`, {
            headers: { 'Accept': 'application/json' },
            signal: AbortSignal.timeout(5000),
        });

        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        return await response.json();
    } catch (error) {
        throw error;
    }
}

// ============================================================================
// DATA UPDATES
// ============================================================================
async function updateHealth() {
    try {
        const data = await fetchAPI('/health');

        setConnectionStatus(true, 'ONLINE');

        // Health value
        if (elements.healthValue) {
            const status = data.status === 'ok' ? 'OK' : data.status.toUpperCase();
            elements.healthValue.textContent = status;
            elements.healthValue.className = 'stat-value ' + (data.status === 'ok' ? 'ok' : 'warning');
        }

        // Health indicator
        if (elements.healthIndicator) {
            elements.healthIndicator.className = 'stat-indicator ' + (data.status === 'ok' ? 'ok' : 'warning');
        }

        // Uptime
        if (elements.uptimeValue && data.uptime_seconds) {
            elements.uptimeValue.textContent = formatUptime(data.uptime_seconds);
        }

        // Filter status
        if (elements.filterStatus) {
            elements.filterStatus.textContent = 'Ativo';
            elements.filterStatus.style.color = 'var(--status-ok)';
        }

        return data;

    } catch (error) {
        setConnectionStatus(false, 'OFFLINE');

        if (elements.healthValue) {
            elements.healthValue.textContent = 'OFFLINE';
            elements.healthValue.className = 'stat-value error';
        }

        if (elements.healthIndicator) {
            elements.healthIndicator.className = 'stat-indicator error';
        }

        if (elements.filterStatus) {
            elements.filterStatus.textContent = 'Desconectado';
            elements.filterStatus.style.color = 'var(--status-error)';
        }

        throw error;
    }
}

async function updateCircuitBreakers() {
    try {
        const data = await fetchAPI('/circuit-breakers');

        if (!elements.cbGrid) return;

        const entries = Object.entries(data);

        if (entries.length === 0) {
            elements.cbGrid.innerHTML = `
                <div class="cb-card closed">
                    <div class="cb-icon"><i class="ph-fill ph-check-circle"></i></div>
                    <div class="cb-name">Nenhum CB</div>
                    <div class="cb-state">N/A</div>
                </div>
            `;

            if (elements.cbSummary) {
                elements.cbSummary.textContent = '0';
            }
            return;
        }

        let closedCount = 0;
        let openCount = 0;

        elements.cbGrid.innerHTML = entries.map(([name, cb]) => {
            const state = cb.state.toLowerCase();
            const stateClass = state === 'closed' ? 'closed' : (state === 'open' ? 'open' : 'half-open');
            const icon = state === 'closed' ? 'check-circle' : (state === 'open' ? 'x-circle' : 'warning-circle');

            if (state === 'closed') closedCount++;
            if (state === 'open') openCount++;

            return `
                <div class="cb-card ${stateClass}">
                    <div class="cb-icon"><i class="ph-fill ph-${icon}"></i></div>
                    <div class="cb-name">${name}</div>
                    <div class="cb-state">${state.toUpperCase()}</div>
                </div>
            `;
        }).join('');

        // Summary
        if (elements.cbSummary) {
            if (openCount > 0) {
                elements.cbSummary.textContent = `${openCount} OPEN`;
                elements.cbSummary.className = 'stat-value error';
            } else {
                elements.cbSummary.textContent = `${entries.length} OK`;
                elements.cbSummary.className = 'stat-value ok';
            }
        }

        if (elements.cbIndicator) {
            elements.cbIndicator.className = 'stat-indicator ' + (openCount > 0 ? 'error' : 'ok');
        }

    } catch (error) {
        if (elements.cbGrid) {
            elements.cbGrid.innerHTML = `
                <div class="cb-loading">
                    <i class="ph-fill ph-wifi-slash"></i>
                    <span>Sem conexão</span>
                </div>
            `;
        }
    }
}

async function updateRules() {
    try {
        const data = await fetchAPI('/rules');

        if (!elements.rulesBody) return;

        if (data.length === 0) {
            elements.rulesBody.innerHTML = `
                <div class="table-loading">Nenhuma regra configurada</div>
            `;
            return;
        }

        elements.rulesBody.innerHTML = data.map(rule => `
            <div class="table-row">
                <span class="rule-name">${rule.name}</span>
                <span class="rule-path">${rule.methods.join(',')} ${rule.path_prefix}*</span>
                <span class="rule-limit">${rule.max_calls}</span>
                <span class="rule-window">${rule.window_seconds}s</span>
            </div>
        `).join('');

    } catch (error) {
        if (elements.rulesBody) {
            elements.rulesBody.innerHTML = `
                <div class="table-loading">Erro ao carregar regras</div>
            `;
        }
    }
}

// ============================================================================
// POLLING
// ============================================================================
async function pollAll() {
    try {
        await Promise.all([
            updateHealth(),
            updateCircuitBreakers(),
            updateRules(),
        ]);
    } catch (error) {
        // Errors handled in individual functions
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
// EVENT HANDLERS
// ============================================================================
function setupEventHandlers() {
    // API Connect button
    if (elements.apiConnect) {
        elements.apiConnect.addEventListener('click', () => {
            const newUrl = elements.apiUrlInput?.value.trim();
            if (newUrl) {
                CONFIG.apiUrl = newUrl;
                localStorage.setItem('caos_api_url', newUrl);

                if (elements.apiUrlDisplay) {
                    elements.apiUrlDisplay.textContent = newUrl.replace('http://', '').replace('https://', '');
                }

                addLogEntry(`API URL alterada: ${newUrl}`, 'info');

                stopPolling();
                startPolling();
            }
        });
    }

    // Refresh CB button
    if (elements.refreshCb) {
        elements.refreshCb.addEventListener('click', () => {
            updateCircuitBreakers();
            addLogEntry('Circuit breakers atualizados', 'info');
        });
    }
}

// ============================================================================
// INITIALIZATION
// ============================================================================
function init() {
    console.log('CAOS Control Center initializing...');
    console.log('API URL:', CONFIG.apiUrl);

    // Set initial values
    if (elements.apiUrlInput) {
        elements.apiUrlInput.value = CONFIG.apiUrl;
    }

    if (elements.apiUrlDisplay) {
        elements.apiUrlDisplay.textContent = CONFIG.apiUrl.replace('http://', '').replace('https://', '');
    }

    // Clock
    updateClock();
    setInterval(updateClock, 1000);

    // Event handlers
    setupEventHandlers();

    // Initial log
    addLogEntry('Sistema inicializado', 'info');
    addLogEntry(`Conectando a ${CONFIG.apiUrl}...`, 'info');

    // Start polling
    startPolling();
}

// Start when DOM is ready
document.addEventListener('DOMContentLoaded', init);
