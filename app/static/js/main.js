// Main JavaScript file for University Portal

// --- SECURITY FIX: Global Fetch Interceptor for CSRF ---
const originalFetch = window.fetch;
window.fetch = async function() {
    let [resource, config] = arguments;
    
    // Only intercept if we have a config object and a mutable method
    if (config && config.method) {
        const method = config.method.toUpperCase();
        if (['POST', 'PUT', 'DELETE', 'PATCH'].includes(method)) {
            const csrfToken = document.querySelector('meta[name="csrf-token"]')?.getAttribute('content');
            if (csrfToken) {
                const newHeaders = new Headers(config.headers || {});
                newHeaders.set('X-CSRFToken', csrfToken);
                config.headers = newHeaders;
            }
        }
    } else if (!config && resource instanceof Request) {
       // Edge case: fetch(new Request('url', {method: 'POST'}))
       const method = resource.method.toUpperCase();
       if (['POST', 'PUT', 'DELETE', 'PATCH'].includes(method)) {
           const csrfToken = document.querySelector('meta[name="csrf-token"]')?.getAttribute('content');
           if (csrfToken) {
               resource.headers.set('X-CSRFToken', csrfToken);
           }
       }
    }
    
    return originalFetch.apply(this, arguments);
};
// -------------------------------------------------------


class UniversityPortal {
    constructor() {
        this.init();
    }

    init() {
        this.initTheme();
        this.initMobileMenu();
        this.initUserMenu();
        this.initFlashMessages();
        this.initSidebar();
        this.initAnimations();
        this.initForms();
        this.initNotifications();
        this.bindEvents();
    }

    // Theme Management
    initTheme() {
        // Use 'themePreference' key to be consistent with the theme menu in base.html
        const savedTheme = localStorage.getItem('themePreference') || 'dark';
        this.setTheme(savedTheme);

        const themeToggle = document.querySelector('.theme-toggle');
        if (themeToggle) {
            themeToggle.addEventListener('click', () => this.toggleTheme());
        }
    }

    setTheme(theme) {
        // Handle 'auto' (or legacy 'system') preference by detecting system preference
        if (theme === 'auto' || theme === 'system') {
            const prefersDark = window.matchMedia('(prefers-color-scheme: dark)').matches;
            document.documentElement.setAttribute('data-theme', prefersDark ? 'dark' : 'light');
        } else {
            document.documentElement.setAttribute('data-theme', theme);
        }

        // Store as 'themePreference' to be consistent with base.html theme menu
        localStorage.setItem('themePreference', theme);
        this.updateThemeIcon(theme);
    }

    toggleTheme() {
        const currentTheme = localStorage.getItem('themePreference') || 'dark';
        const themes = ['light', 'dark', 'auto'];
        const currentIndex = themes.indexOf(currentTheme);
        const nextTheme = themes[(currentIndex + 1) % themes.length];

        this.setTheme(nextTheme);

        // Update theme on server if user is logged in
        if (document.querySelector('[data-user-authenticated]')) {
            this.updateThemeOnServer(nextTheme);
        }
    }

    updateThemeIcon(theme) {
        const themeToggle = document.querySelector('.theme-toggle');
        if (!themeToggle) return;

        const icons = {
            light: '☀️',
            dark: '🌙',
            auto: '🖥️'
        };

        themeToggle.innerHTML = icons[theme] || icons.auto;
        themeToggle.title = `Current theme: ${theme}`;
    }

    async updateThemeOnServer(theme) {
        try {
            const response = await fetch('/api/theme', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({ theme })
            });

            if (!response.ok) {
                console.error('Failed to update theme on server');
            }
        } catch (error) {
            console.error('Error updating theme:', error);
        }
    }

    // Mobile Menu
    initMobileMenu() {
        const menuToggle = document.querySelector('.menu-toggle');
        const sidebar = document.querySelector('.sidebar');
        const overlay = document.createElement('div');
        overlay.className = 'sidebar-overlay';
        document.body.appendChild(overlay);

        if (menuToggle && sidebar) {
            menuToggle.addEventListener('click', () => {
                sidebar.classList.toggle('active');
                overlay.classList.toggle('active');
                document.body.classList.toggle('sidebar-open');
            });

            overlay.addEventListener('click', () => {
                sidebar.classList.remove('active');
                overlay.classList.remove('active');
                document.body.classList.remove('sidebar-open');
            });
        }
    }

    // Sidebar
    initSidebar() {
        const sidebar = document.querySelector('.sidebar');
        const sidebarToggle = document.querySelector('.sidebar-toggle');
        const body = document.body;

        if (sidebar && sidebarToggle) {
            // Check for saved state
            if (localStorage.getItem('sidebar-collapsed') === 'true') {
                body.classList.add('sidebar-collapsed');
            }

            sidebarToggle.addEventListener('click', () => {
                body.classList.toggle('sidebar-collapsed');
                localStorage.setItem('sidebar-collapsed', body.classList.contains('sidebar-collapsed'));
            });
        }
    }

    // User Menu
    initUserMenu() {
        const userMenu = document.querySelector('.user-menu');
        const userMenuToggle = document.querySelector('.user-menu-toggle');

        if (userMenu && userMenuToggle) {
            userMenuToggle.addEventListener('click', (e) => {
                e.stopPropagation();
                userMenu.classList.toggle('active');
            });

            document.addEventListener('click', (e) => {
                if (!userMenu.contains(e.target)) {
                    userMenu.classList.remove('active');
                }
            });
        }
    }

    // Flash Messages
    initFlashMessages() {
        const flashMessages = document.querySelectorAll('.flash-message');

        flashMessages.forEach((message, index) => {
            // Auto dismiss after 5 seconds
            setTimeout(() => {
                this.dismissFlashMessage(message);
            }, 5000 + (index * 200)); // Stagger dismissal

            // Add click to dismiss
            message.addEventListener('click', () => {
                this.dismissFlashMessage(message);
            });

            // Add close button
            const closeBtn = document.createElement('button');
            closeBtn.innerHTML = '×';
            closeBtn.className = 'flash-close';
            closeBtn.style.cssText = `
                background: none;
                border: none;
                color: inherit;
                font-size: 1.5rem;
                cursor: pointer;
                margin-left: auto;
                padding-left: 1rem;
            `;
            closeBtn.addEventListener('click', (e) => {
                e.stopPropagation();
                this.dismissFlashMessage(message);
            });
            message.appendChild(closeBtn);
        });
    }

    dismissFlashMessage(message) {
        message.style.animation = 'slideOut 0.3s ease-out forwards';
        setTimeout(() => {
            if (message.parentNode) {
                message.parentNode.removeChild(message);
            }
        }, 300);
    }

    // Animations
    initAnimations() {
        // Add js-enabled to body for progressive CSS
        document.body.classList.add('js-enabled');

        // Progressive load fades
        const observerOptions = {
            threshold: 0,
            rootMargin: '0px 0px 200px 0px' // Eager loading trigger
        };

        const observer = new IntersectionObserver((entries, obs) => {
            entries.forEach(entry => {
                if (entry.isIntersecting) {
                    entry.target.classList.add('fade-up-active');
                    obs.unobserve(entry.target); // Run once and detach
                }
            });
        }, observerOptions);

        // Observe elements with fade-up-ready (and all legacy card types that we'll update)
        const selectors = '.fade-up-ready, .card, .settings-card, .activity-card, .stat-card, .account-card, .attendance-card, .event-card, .login-card, .register-card, .summary-card';
        document.querySelectorAll(selectors).forEach((el) => {
            // If they are legacy cards without the class yet, we add it to ensure they get the correct CSS
            // though adding it in JS causes a minor FOUC, so we should update the HTML templates too.
            if (!el.classList.contains('fade-up-ready')) {
                el.classList.add('fade-up-ready');
            }
            observer.observe(el);
        });

        // Progress bar animations
        this.animateProgressBars();
    }

    animateProgressBars() {
        const progressBars = document.querySelectorAll('.progress-bar');

        progressBars.forEach(bar => {
            const width = bar.getAttribute('data-width') || bar.style.width;
            bar.style.width = '0%';

            setTimeout(() => {
                bar.style.width = width;
            }, 300);
        });
    }

    // Forms
    initForms() {
        // Auto-save forms (for settings)
        const autoSaveForms = document.querySelectorAll('[data-auto-save]');

        autoSaveForms.forEach(form => {
            const inputs = form.querySelectorAll('input, select, textarea');

            inputs.forEach(input => {
                input.addEventListener('change', () => {
                    this.autoSaveForm(form);
                });
            });
        });

        // Form validation
        const forms = document.querySelectorAll('form[data-validate]');
        forms.forEach(form => {
            form.addEventListener('submit', (e) => {
                if (!this.validateForm(form)) {
                    e.preventDefault();
                }
            });
        });
    }

    async autoSaveForm(form) {
        const formData = new FormData(form);
        const data = Object.fromEntries(formData);

        try {
            const response = await fetch(form.action, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify(data)
            });

            if (response.ok) {
                this.showNotification('Settings saved automatically', 'success');
            }
        } catch (error) {
            console.error('Auto-save failed:', error);
        }
    }

    validateForm(form) {
        const requiredFields = form.querySelectorAll('[required]');
        let isValid = true;

        requiredFields.forEach(field => {
            if (!field.value.trim()) {
                this.showFieldError(field, 'This field is required');
                isValid = false;
            } else {
                this.clearFieldError(field);
            }
        });

        return isValid;
    }

    showFieldError(field, message) {
        this.clearFieldError(field);

        const errorElement = document.createElement('div');
        errorElement.className = 'field-error';
        errorElement.textContent = message;
        errorElement.style.cssText = `
            color: var(--error-color);
            font-size: 0.875rem;
            margin-top: 0.25rem;
        `;

        field.parentNode.appendChild(errorElement);
        field.style.borderColor = 'var(--error-color)';
    }

    clearFieldError(field) {
        const existingError = field.parentNode.querySelector('.field-error');
        if (existingError) {
            existingError.remove();
        }
        field.style.borderColor = '';
    }

    // Notification Bell System
    initNotifications() {
        const bell = document.getElementById('notificationBell');
        const bellToggle = document.getElementById('notificationBellToggle');
        const dropdown = document.getElementById('notificationDropdown');
        const markAllBtn = document.getElementById('markAllRead');

        if (!bell || !bellToggle) return;

        // Toggle dropdown
        bellToggle.addEventListener('click', (e) => {
            e.stopPropagation();
            bell.classList.toggle('active');

            if (bell.classList.contains('active')) {
                this.loadNotifications();
            }
        });

        // Close dropdown when clicking outside
        document.addEventListener('click', (e) => {
            if (bell && !bell.contains(e.target)) {
                bell.classList.remove('active');
            }
        });

        // Mark all as read
        if (markAllBtn) {
            markAllBtn.addEventListener('click', async (e) => {
                e.stopPropagation();
                await this.markAllNotificationsRead();
            });
        }

        // Initial badge count
        this.updateNotificationBadge();

        // Refresh badge periodically
        setInterval(() => this.updateNotificationBadge(), 60000);
    }

    async loadNotifications() {
        const list = document.getElementById('notificationList');
        const empty = document.getElementById('notificationEmpty');

        if (!list) return;

        // Show loading
        list.innerHTML = `
            <div class="notification-loading">
                <i class="fas fa-spinner fa-spin"></i>
                <span>Loading...</span>
            </div>
        `;
        empty.style.display = 'none';

        try {
            const response = await fetch('/api/notifications?limit=20');
            const data = await response.json();

            if (data.success && data.notifications.length > 0) {
                list.innerHTML = data.notifications.map(n => this.renderNotificationItem(n)).join('');
                empty.style.display = 'none';

                // Add click handlers to items
                list.querySelectorAll('.notification-item').forEach(item => {
                    item.addEventListener('click', () => {
                        const id = item.dataset.id;
                        this.markNotificationRead(id);
                        item.classList.remove('unread');
                    });
                });
            } else {
                list.innerHTML = '';
                empty.style.display = 'flex';
            }
        } catch (error) {
            console.error('Failed to load notifications:', error);
            list.innerHTML = '<div class="notification-empty"><span>Failed to load</span></div>';
        }
    }

    renderNotificationItem(notification) {
        let createdAt = notification.created_at;
        if (createdAt && !createdAt.endsWith('Z') && !createdAt.includes('+')) {
            createdAt += 'Z';
        }
        const timeAgo = this.getTimeAgo(new Date(createdAt));

        const typeMap = {
            'overdue_warning':      { cls: 'type-overdue',     icon: '<i class="fas fa-exclamation-triangle"></i>' },
            'due_24h':              { cls: 'type-due24h',      icon: '<i class="fas fa-fire"></i>' },
            'due_3d':               { cls: 'type-due3d',       icon: '<i class="fas fa-hourglass-half"></i>' },
            'new_assignment':       { cls: 'type-new',         icon: '<i class="fas fa-tasks"></i>' },
            'absent_scheduled':     { cls: 'type-attendance',  icon: '<i class="fas fa-times-circle"></i>' },
            'absent_unscheduled':   { cls: 'type-attendance',  icon: '<i class="fas fa-exclamation-circle"></i>' },
            'present_scheduled':    { cls: 'type-attendance',  icon: '<i class="fas fa-check-circle"></i>' },
            'present_unscheduled':  { cls: 'type-attendance',  icon: '<i class="fas fa-check"></i>' },
            'system_info':          { cls: 'type-system',      icon: '<i class="fas fa-info-circle"></i>' },
            'system_warning':       { cls: 'type-system',      icon: '<i class="fas fa-exclamation-circle"></i>' },
        };
        const { cls, icon } = typeMap[notification.type] || { cls: 'type-system', icon: '<i class="fas fa-bell"></i>' };
        const unreadDot = !notification.read ? `<span class="notification-unread-dot"></span>` : '';

        return `
            <div class="notification-item ${notification.read ? '' : 'unread'} ${cls}" data-id="${notification.id}">
                <div class="notification-icon ${cls}">${icon}</div>
                <div class="notification-content">
                    <div class="notification-item-title">${notification.title}</div>
                    <div class="notification-item-body">${notification.message}</div>
                    <div class="notification-item-meta">
                        <span class="notification-item-time">${timeAgo}</span>
                        ${unreadDot}
                    </div>
                </div>
            </div>
        `;
    }

    getTimeAgo(date) {
        const seconds = Math.floor((new Date() - date) / 1000);

        if (seconds < 60) return 'Just now';
        if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
        if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`;
        if (seconds < 604800) return `${Math.floor(seconds / 86400)}d ago`;
        return date.toLocaleDateString();
    }

    async updateNotificationBadge() {
        const badge = document.getElementById('notificationBadge');
        if (!badge) return;

        try {
            const response = await fetch('/api/notifications/unread-count');
            const data = await response.json();

            if (data.success) {
                if (data.unread_count > 0) {
                    badge.textContent = data.unread_count > 9 ? '9+' : data.unread_count;
                    badge.style.display = 'flex';
                } else {
                    badge.style.display = 'none';
                }
            }
        } catch (error) {
            // Silently fail
        }
    }

    async markNotificationRead(id) {
        try {
            await fetch(`/api/notifications/${id}/read`, { method: 'POST' });
            this.updateNotificationBadge();
        } catch (error) {
            console.error('Failed to mark notification as read:', error);
        }
    }

    async markAllNotificationsRead() {
        try {
            await fetch('/api/notifications/read-all', { method: 'POST' });
            this.updateNotificationBadge();

            // Update UI
            document.querySelectorAll('.notification-item.unread').forEach(item => {
                item.classList.remove('unread');
            });

            this.showNotification('All notifications marked as read', 'success');
        } catch (error) {
            console.error('Failed to mark all as read:', error);
        }
    }

    // Toast Notifications
    showNotification(message, type = 'info') {
        const notification = document.createElement('div');
        notification.className = `flash-message ${type}`;
        notification.textContent = message;

        const container = document.querySelector('.flash-messages');
        if (container) {
            container.appendChild(notification);

            setTimeout(() => {
                this.dismissFlashMessage(notification);
            }, 3000);
        }
    }

    // Event Bindings
    bindEvents() {
        // System theme preference change - respond when user has 'auto' selected
        window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => {
            const currentPref = localStorage.getItem('themePreference');
            if (currentPref === 'auto' || currentPref === 'system') {
                this.setTheme('auto');
            }
        });

        // Keyboard shortcuts
        document.addEventListener('keydown', (e) => {
            // Ctrl/Cmd + K for quick navigation (future feature)
            if ((e.ctrlKey || e.metaKey) && e.key === 'k') {
                e.preventDefault();
                // Open search/navigation modal
            }

            // Escape to close menus
            if (e.key === 'Escape') {
                document.querySelector('.user-menu')?.classList.remove('active');
                document.querySelector('.sidebar')?.classList.remove('active');
            }
        });

        // Smooth scrolling for anchor links
        document.querySelectorAll('a[href^="#"]').forEach(anchor => {
            anchor.addEventListener('click', function (e) {
                e.preventDefault();
                const target = document.querySelector(this.getAttribute('href'));
                if (target) {
                    target.scrollIntoView({
                        behavior: 'smooth',
                        block: 'start'
                    });
                }
            });
        });
    }
}

// Chart utilities (for attendance visualization)
class ChartUtils {
    static createAttendanceChart(canvasId, data) {
        const canvas = document.getElementById(canvasId);
        if (!canvas) return;

        const ctx = canvas.getContext('2d');

        // Simple pie chart implementation
        const total = data.reduce((sum, item) => sum + item.value, 0);
        let currentAngle = -Math.PI / 2;

        const colors = ['#10b981', '#f59e0b', '#ef4444'];

        data.forEach((item, index) => {
            const sliceAngle = (item.value / total) * 2 * Math.PI;

            ctx.beginPath();
            ctx.arc(100, 100, 80, currentAngle, currentAngle + sliceAngle);
            ctx.lineTo(100, 100);
            ctx.fillStyle = colors[index % colors.length];
            ctx.fill();

            currentAngle += sliceAngle;
        });
    }

    static createProgressChart(canvasId, percentage) {
        const canvas = document.getElementById(canvasId);
        if (!canvas) return;

        const ctx = canvas.getContext('2d');
        const centerX = canvas.width / 2;
        const centerY = canvas.height / 2;
        const radius = 60;

        // Background circle
        ctx.beginPath();
        ctx.arc(centerX, centerY, radius, 0, 2 * Math.PI);
        ctx.strokeStyle = '#e2e8f0';
        ctx.lineWidth = 8;
        ctx.stroke();

        // Progress arc
        ctx.beginPath();
        ctx.arc(centerX, centerY, radius, -Math.PI / 2, (-Math.PI / 2) + (2 * Math.PI * percentage / 100));
        ctx.strokeStyle = '#2563eb';
        ctx.lineWidth = 8;
        ctx.lineCap = 'round';
        ctx.stroke();

        // Center text
        ctx.fillStyle = '#1e293b';
        ctx.font = 'bold 24px Inter';
        ctx.textAlign = 'center';
        ctx.fillText(`${percentage}%`, centerX, centerY + 8);
    }
}

// GPA Calculator utilities
class GPACalculator {
    static grades = {
        'A+': 4.0, 'A': 4.0, 'A-': 3.7,
        'B+': 3.3, 'B': 3.0, 'B-': 2.7,
        'C+': 2.3, 'C': 2.0, 'C-': 1.7,
        'D+': 1.3, 'D': 1.0, 'F': 0.0
    };

    static calculate(courses) {
        let totalPoints = 0;
        let totalCredits = 0;

        courses.forEach(course => {
            const grade = course.grade.toUpperCase();
            const credits = parseFloat(course.credits);

            if (this.grades.hasOwnProperty(grade) && credits > 0) {
                totalPoints += this.grades[grade] * credits;
                totalCredits += credits;
            }
        });

        return totalCredits > 0 ? totalPoints / totalCredits : 0;
    }

    static getGradeColor(gpa) {
        if (gpa >= 3.5) return '#10b981'; // green
        if (gpa >= 3.0) return '#f59e0b'; // yellow
        if (gpa >= 2.5) return '#f97316'; // orange
        return '#ef4444'; // red
    }
}

// Initialize the application when DOM is loaded
document.addEventListener('DOMContentLoaded', () => {
    window.portal = new UniversityPortal();

    // Add some CSS animations dynamically
    const style = document.createElement('style');
    style.textContent = `
        @keyframes slideOut {
            to {
                opacity: 0;
                transform: translateX(100%);
            }
        }
        
        .sidebar-overlay {
            position: fixed;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            background-color: rgba(0, 0, 0, 0.5);
            opacity: 0;
            visibility: hidden;
            transition: all 0.3s ease;
            z-index: 999;
        }
        
        .sidebar-overlay.active {
            opacity: 1;
            visibility: visible;
        }
        
        @media (min-width: 769px) {
            .sidebar-overlay {
                display: none;
            }
        }
    `;
    document.head.appendChild(style);
});

// Timetable refresh function
function refreshTimetable() {
    const refreshBtn = document.getElementById('refreshDataToggle') || document.querySelector('button[onclick="refreshTimetable()"]');
    if (refreshBtn) {
        refreshBtn.classList.add('is-refreshing');
        refreshBtn.disabled = true;
    }
    const mainContent = document.getElementById('timetableMainContent');
    const loadingState = document.getElementById('timetableLoadingState');
    if (mainContent) mainContent.style.display = 'none';
    if (loadingState) {
        loadingState.style.display = 'flex';
        window.scrollTo({ top: 0, behavior: 'smooth' });
    }

    const STEPS = [
        { id: 'step-connect',  pct: 22, status: 'Connecting to CMS Portal...', subtext: 'Authenticating session with university portal...' },
        { id: 'step-scrape',   pct: 54, status: 'Fetching course registrations...', subtext: 'Scraping registered class schedule data...' },
        { id: 'step-analyze',  pct: 82, status: 'Parsing weekly class schedule...', subtext: 'Organizing time slots, days, and rooms...' },
        { id: 'step-finalize', pct: 96, status: 'Finalizing timetable view...', subtext: 'Preparing weekly calendar view...' }
    ];

    const progressBar = document.getElementById('unifiedProgressBar');
    const statusText = document.getElementById('loadingStatusText');
    const subtextEl = document.getElementById('loadingSubtext');

    function setStepState(index) {
        const step = STEPS[index];
        if (statusText) statusText.textContent = step.status;
        if (subtextEl) subtextEl.textContent = step.subtext;
        if (progressBar) progressBar.style.width = step.pct + '%';

        STEPS.forEach((s, idx) => {
            const el = document.getElementById(s.id);
            if (!el) return;
            el.classList.remove('active', 'done');
            if (idx < index) el.classList.add('done');
            else if (idx === index) el.classList.add('active');
        });
    }

    setStepState(0);
    setTimeout(() => setStepState(1), 1400);
    setTimeout(() => setStepState(2), 3400);
    setTimeout(() => setStepState(3), 5800);

    window.location.href = '/timetable/refresh';
}

// Attendance refresh function
function refreshAttendance() {
    const refreshBtn = document.getElementById('refreshDataToggle') || document.querySelector('button[onclick="refreshAttendance()"]');
    if (refreshBtn) {
        refreshBtn.classList.add('is-refreshing');
        refreshBtn.disabled = true;
    }
    const mainContent = document.getElementById('attendanceMainContent');
    const loadingState = document.getElementById('attendanceLoadingState');
    if (mainContent) mainContent.style.display = 'none';
    if (loadingState) {
        loadingState.style.display = 'flex';
        window.scrollTo({ top: 0, behavior: 'smooth' });
    }

    const STEPS = [
        { id: 'step-connect',  pct: 22, status: 'Connecting to CMS Portal...', subtext: 'Authenticating session with university portal...' },
        { id: 'step-scrape',   pct: 54, status: 'Fetching attendance records...', subtext: 'Scraping course lecture counts & percentages...' },
        { id: 'step-analyze',  pct: 82, status: 'Analyzing updates & thresholds...', subtext: 'Comparing against previous snapshot...' },
        { id: 'step-finalize', pct: 96, status: 'Finalizing attendance data...', subtext: 'Preparing dashboard view...' }
    ];

    const progressBar = document.getElementById('unifiedProgressBar');
    const statusText = document.getElementById('loadingStatusText');
    const subtextEl = document.getElementById('loadingSubtext');

    function setStepState(index) {
        const step = STEPS[index];
        if (statusText) statusText.textContent = step.status;
        if (subtextEl) subtextEl.textContent = step.subtext;
        if (progressBar) progressBar.style.width = step.pct + '%';

        STEPS.forEach((s, idx) => {
            const el = document.getElementById(s.id);
            if (!el) return;
            el.classList.remove('active', 'done');
            if (idx < index) el.classList.add('done');
            else if (idx === index) el.classList.add('active');
        });
    }

    setStepState(0);
    setTimeout(() => setStepState(1), 1400);
    setTimeout(() => setStepState(2), 3400);
    setTimeout(() => setStepState(3), 5800);

    window.location.href = '/attendance/refresh';
}

// Community Service refresh function
function refreshCommunityService() {
    const refreshBtn = document.getElementById('refreshDataToggle') || document.querySelector('button[onclick="refreshCommunityService()"]');
    if (refreshBtn) {
        refreshBtn.classList.add('is-refreshing');
        refreshBtn.disabled = true;
    }
    const mainContent = document.getElementById('cspMainContent');
    const loadingState = document.getElementById('cspLoadingState');
    if (mainContent) mainContent.style.display = 'none';
    if (loadingState) {
        loadingState.style.display = 'flex';
        window.scrollTo({ top: 0, behavior: 'smooth' });
    }

    const STEPS = [
        { id: 'step-connect',  pct: 22, status: 'Connecting to CMS Portal...', subtext: 'Authenticating session with university portal...' },
        { id: 'step-scrape',   pct: 54, status: 'Fetching community service logs...', subtext: 'Scraping hours, project entries & statuses...' },
        { id: 'step-analyze',  pct: 82, status: 'Calculating verified hours & progress...', subtext: 'Verifying completed and required thresholds...' },
        { id: 'step-finalize', pct: 96, status: 'Finalizing CSP status...', subtext: 'Preparing updated progress summary...' }
    ];

    const progressBar = document.getElementById('unifiedProgressBar');
    const statusText = document.getElementById('loadingStatusText');
    const subtextEl = document.getElementById('loadingSubtext');

    function setStepState(index) {
        const step = STEPS[index];
        if (statusText) statusText.textContent = step.status;
        if (subtextEl) subtextEl.textContent = step.subtext;
        if (progressBar) progressBar.style.width = step.pct + '%';

        STEPS.forEach((s, idx) => {
            const el = document.getElementById(s.id);
            if (!el) return;
            el.classList.remove('active', 'done');
            if (idx < index) el.classList.add('done');
            else if (idx === index) el.classList.add('active');
        });
    }

    setStepState(0);
    setTimeout(() => setStepState(1), 1400);
    setTimeout(() => setStepState(2), 3400);
    setTimeout(() => setStepState(3), 5800);

    window.location.href = '/community-service/refresh';
}

// Provisional Results refresh function
function refreshProvisionalResults() {
    const refreshBtn = document.getElementById('refreshDataToggle') || document.querySelector('button[onclick="refreshProvisionalResults()"]');
    if (refreshBtn) {
        refreshBtn.classList.add('is-refreshing');
        refreshBtn.disabled = true;
    }
    const mainContent = document.getElementById('resultsMainContent');
    const loadingState = document.getElementById('resultsLoadingState');
    if (mainContent) mainContent.style.display = 'none';
    if (loadingState) {
        loadingState.style.display = 'flex';
        window.scrollTo({ top: 0, behavior: 'smooth' });
    }

    const STEPS = [
        { id: 'step-connect',  pct: 22, status: 'Connecting to CMS Portal...', subtext: 'Authenticating session with university portal...' },
        { id: 'step-scrape',   pct: 54, status: 'Fetching exam & provisional grades...', subtext: 'Scraping latest semester exam transcripts...' },
        { id: 'step-analyze',  pct: 82, status: 'Calculating GPA, CGPA & credit hours...', subtext: 'Tabulating grade points and cumulative index...' },
        { id: 'step-finalize', pct: 96, status: 'Finalizing academic summary...', subtext: 'Preparing provisional results breakdown...' }
    ];

    const progressBar = document.getElementById('unifiedProgressBar');
    const statusText = document.getElementById('loadingStatusText');
    const subtextEl = document.getElementById('loadingSubtext');

    function setStepState(index) {
        const step = STEPS[index];
        if (statusText) statusText.textContent = step.status;
        if (subtextEl) subtextEl.textContent = step.subtext;
        if (progressBar) progressBar.style.width = step.pct + '%';

        STEPS.forEach((s, idx) => {
            const el = document.getElementById(s.id);
            if (!el) return;
            el.classList.remove('active', 'done');
            if (idx < index) el.classList.add('done');
            else if (idx === index) el.classList.add('active');
        });
    }

    setStepState(0);
    setTimeout(() => setStepState(1), 1400);
    setTimeout(() => setStepState(2), 3400);
    setTimeout(() => setStepState(3), 5800);

    window.location.href = '/provisional-results/refresh';
}

// Export for use in other modules
if (typeof module !== 'undefined' && module.exports) {
    module.exports = { UniversityPortal, ChartUtils, GPACalculator };
}
