/**
 * LMS Signal Widget for Loading Screens
 * Displays LMS server connectivity status during data loading
 */

class LMSSignalWidget {
    constructor() {
        this.statusCache = null;
        this.updateInterval = null;
    }

    /**
     * Fetch LMS status from API
     */
    async fetchStatus() {
        try {
            const response = await fetch('/lms/api/lms-status');
            const data = await response.json();
            this.statusCache = data;
            return data;
        } catch (error) {
            console.error('Failed to fetch LMS status:', error);
            return {
                status: 'error',
                response_time: 0,
                signal_strength: 'unknown',
                message: 'Unable to check status'
            };
        }
    }

    /**
     * Update signal bars based on status
     */
    updateSignalBars(element, signalStrength) {
        if (!element) return;
        element.className = 'signal-bars signal-' + signalStrength;
    }

    /**
     * Format time ago string
     */
    formatTimeAgo(lastCheck) {
        if (!lastCheck) return '';
        
        const lastCheckTime = new Date(lastCheck);
        const now = new Date();
        const diffSeconds = Math.floor((now - lastCheckTime) / 1000);
        
        if (diffSeconds < 60) {
            return 'Just now';
        } else if (diffSeconds < 3600) {
            const minutes = Math.floor(diffSeconds / 60);
            return minutes + ' minute' + (minutes > 1 ? 's' : '') + ' ago';
        } else {
            const hours = Math.floor(diffSeconds / 3600);
            return hours + ' hour' + (hours > 1 ? 's' : '') + ' ago';
        }
    }

    /**
     * Create loading screen signal widget HTML
     */
    createLoadingWidget() {
        return `
            <div class="lms-loading-signal" id="lmsLoadingSignal">
                <div class="signal-bars signal-unknown" id="loadingSignalBars">
                    <span class="signal-bar"></span>
                    <span class="signal-bar"></span>
                    <span class="signal-bar"></span>
                </div>
                <div class="lms-loading-signal-info">
                    <div class="lms-loading-signal-status" id="loadingSignalStatus">
                        Checking LMS connection...
                    </div>
                    <div class="lms-loading-signal-time" id="loadingSignalTime"></div>
                </div>
            </div>
        `;
    }

    /**
     * Update loading screen widget
     */
    async updateLoadingWidget() {
        const signalBars = document.getElementById('loadingSignalBars');
        const statusText = document.getElementById('loadingSignalStatus');
        const timeText = document.getElementById('loadingSignalTime');
        
        if (!signalBars || !statusText || !timeText) return;
        
        const data = await this.fetchStatus();
        
        // Update signal bars
        this.updateSignalBars(signalBars, data.signal_strength);
        
        // Update status text
        statusText.textContent = data.message;
        
        // Update time text
        let timeString = '';
        if (data.response_time > 0) {
            timeString = 'Response time: ' + Math.round(data.response_time) + 'ms';
        }
        if (data.last_check) {
            const timeAgo = this.formatTimeAgo(data.last_check);
            timeString += (timeString ? ' • ' : '') + 'Last checked: ' + timeAgo;
        }
        timeText.textContent = timeString;
    }

    /**
     * Show loading screen with signal widget
     */
    showLoadingScreen(message = 'Loading LMS data...') {
        const existingOverlay = document.getElementById('lmsLoadingOverlay');
        if (existingOverlay) {
            existingOverlay.remove();
        }

        const loadingHTML = `
            <div class="lms-loading-overlay" id="lmsLoadingOverlay">
                <div class="lms-loading-content">
                    <div class="loading-spinner"></div>
                    <p class="loading-message">${message}</p>
                    ${this.createLoadingWidget()}
                </div>
            </div>
        `;
        
        document.body.insertAdjacentHTML('beforeend', loadingHTML);
        
        // Update signal widget
        this.updateLoadingWidget();
        
        // Update every 5 seconds while loading
        this.updateInterval = setInterval(() => {
            this.updateLoadingWidget();
        }, 5000);
    }

    /**
     * Hide loading screen
     */
    hideLoadingScreen() {
        const overlay = document.getElementById('lmsLoadingOverlay');
        if (overlay) {
            overlay.remove();
        }
        
        if (this.updateInterval) {
            clearInterval(this.updateInterval);
            this.updateInterval = null;
        }
    }

    /**
     * Add signal widget to existing loading screen
     */
    addToExistingLoadingScreen(containerId) {
        const container = document.getElementById(containerId);
        if (!container) return;
        
        container.insertAdjacentHTML('beforeend', this.createLoadingWidget());
        this.updateLoadingWidget();
    }
}

// Create global instance
window.lmsSignalWidget = new LMSSignalWidget();

// Add CSS for loading overlay if not already present
if (!document.getElementById('lmsLoadingStyles')) {
    // Wait for DOM to be ready to get computed styles
    const addStyles = () => {
        // Get the actual header height from CSS variable or use default
        let headerHeight = '60px';
        try {
            const computed = getComputedStyle(document.documentElement).getPropertyValue('--header-height').trim();
            if (computed) {
                headerHeight = computed;
            }
        } catch (e) {
            console.warn('Could not get header height, using default');
        }
        
        const style = document.createElement('style');
        style.id = 'lmsLoadingStyles';
        style.textContent = `
            .lms-loading-overlay {
                position: fixed;
                top: ${headerHeight} !important;
                left: 0;
                right: 0;
                bottom: 0;
                background: rgba(0, 0, 0, 0.5);
                backdrop-filter: blur(8px);
                display: flex;
                align-items: center;
                justify-content: center;
                z-index: 9999;
            }
        
        .lms-loading-content {
            background: var(--bg-primary);
            border: 1px solid var(--border-color);
            border-radius: 16px;
            padding: 2rem;
            min-width: 320px;
            max-width: 90vw;
            text-align: center;
            box-shadow: 0 20px 60px rgba(0, 0, 0, 0.3);
        }
        
        .loading-spinner {
            width: 48px;
            height: 48px;
            border: 4px solid var(--bg-tertiary);
            border-top: 4px solid var(--primary-color);
            border-radius: 50%;
            animation: spin 1s linear infinite;
            margin: 0 auto 1rem;
        }
        
        @keyframes spin {
            0% { transform: rotate(0deg); }
            100% { transform: rotate(360deg); }
        }
        
        .loading-message {
            font-size: 1rem;
            font-weight: 500;
            color: var(--text-primary);
            margin: 0 0 1rem 0;
        }
    `;
        document.head.appendChild(style);
    };
    
    // Call immediately if DOM is ready, otherwise wait
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', addStyles);
    } else {
        addStyles();
    }
}
