// LMS Lecture Notes - New Implementation
// Mimics the original LMS behavior with dropdown selection

console.log('LMS Lecture Notes script loaded');

document.addEventListener('DOMContentLoaded', function() {
    console.log('DOM Content Loaded - Initializing lecture notes');
    
    // DOM Elements
    const semesterSelect = document.getElementById('semesterSelect');
    const courseSelect = document.getElementById('courseSelect');
    const refreshButton = document.getElementById('refreshLectureNotes');
    const downloadAllBtn = document.getElementById('downloadAllBtn');
    const loadingOverlay = document.getElementById('loadingOverlay');
    const notificationBanner = document.getElementById('notificationBanner');
    const lectureNotesContainer = document.getElementById('lectureNotesContainer');
    const emptyState = document.getElementById('emptyState');
    const errorState = document.getElementById('errorState');
    const errorMessage = document.getElementById('errorMessage');
    const tableBody = document.getElementById('lectureNotesTableBody');
    
    console.log('Elements found:', {
        semesterSelect: !!semesterSelect,
        courseSelect: !!courseSelect,
        refreshButton: !!refreshButton
    });
    
    // State
    let currentSemester = null;
    let currentCourse = null;
    let tsSemester = null;
    let tsCourse = null;
    let lmsWatchdogTimer = null;
    let lectureDownloadRequest = 0;
    let lectureDownloadAttempt = 0;
    let lectureDownloadSingle = null;
    let lectureDownloadProxyCheck = null;
    let lectureDownloadResetTimer = null;
    const lectureDownloadFrames = new Map();

    function startLMSWatchdog(delayMs = 6000) {
        clearLMSWatchdog();
        lmsWatchdogTimer = setTimeout(() => {
            if (typeof window.showLMSBanner === 'function') {
                window.showLMSBanner(
                    'LMS responding slowly',
                    'LMS+ is on it — just waiting for university servers to answer.'
                );
            }
        }, delayMs);
    }

    function clearLMSWatchdog() {
        if (lmsWatchdogTimer) {
            clearTimeout(lmsWatchdogTimer);
            lmsWatchdogTimer = null;
        }
        if (typeof window.hideLMSBanner === 'function') {
            window.hideLMSBanner();
        }
    }
    
    // Initialize
    init();
    
    function init() {
        console.log('Initializing lecture notes interface');
        
        // Show empty state initially
        showEmptyState();
        
        // Handle semester change - update course dropdown
        if (semesterSelect) {
            tsSemester = new TomSelect(semesterSelect, {
                create: false,
                controlInput: null,
                maxOptions: null,
                dropdownParent: 'body'
            });
            
            semesterSelect.addEventListener('change', function() {
                console.log('Semester changed:', this.value);
                const semester = this.value;
                
                if (tsSemester) { tsSemester.close(); tsSemester.blur(); }
                
                resetCourseSelection();
                if (semester) {
                    updateCoursesForSemester(semester);
                }
            });
            
            // Auto-load courses for initial semester
            if (semesterSelect.value) {
                updateCoursesForSemester(semesterSelect.value);
            }
        }
        
        // Auto-load lecture notes when course is selected
        if (courseSelect) {
            tsCourse = new TomSelect(courseSelect, {
                placeholder: 'Select a course...',
                allowEmptyOption: false,
                create: false,
                controlInput: null,
                maxOptions: null,
                dropdownParent: 'body'
            });
            
            courseSelect.addEventListener('change', function() {
                console.log('Course selected:', this.value);
                const course = this.value;
                const semester = semesterSelect ? semesterSelect.value : null;
                
                if (tsCourse) { tsCourse.close(); tsCourse.blur(); }
                
                if (semester && course) {
                    loadLectureNotes(semester, course);
                } else {
                    showEmptyState();
                }
            });
        }
        
        // Refresh button
        if (refreshButton) {
            refreshButton.addEventListener('click', function() {
                if (semesterSelect.value && courseSelect.value) {
                    loadLectureNotes(semesterSelect.value, courseSelect.value, true);
                }
            });
        }

        // Download All button
        if (downloadAllBtn) {
            downloadAllBtn.addEventListener('click', function() {
                if (currentCourse) {
                    downloadAllAsZip(currentSemester, currentCourse);
                }
            });
        }

        // Smart Retry button for error state
        const retryBtn = document.getElementById('retryButton');
        if (retryBtn) {
            retryBtn.addEventListener('click', function(e) {
                e.preventDefault();
                if (currentCourse) {
                    loadLectureNotes(currentSemester, currentCourse, true);
                } else if (semesterSelect && semesterSelect.value) {
                    updateCoursesForSemester(semesterSelect.value);
                } else {
                    location.reload();
                }
            });
        }
    }
    
    function updateRefreshVisibility() {
        const actions = document.getElementById('courseRefreshActions');
        if (actions) actions.style.display = semesterSelect.value && courseSelect.value ? 'block' : 'none';
    }

    function resetCourseSelection() {
        currentSemester = null;
        currentCourse = null;
        if (tsCourse) {
            tsCourse.clear(true);
            tsCourse.clearOptions();
            tsCourse.setValue('', true);
        } else {
            courseSelect.innerHTML = '<option value="" disabled hidden selected>Select a course...</option>';
        }
        showEmptyState();
    }

    function updateCoursesForSemester(semester) {
        console.log('Updating courses for semester:', semester);
        
        // Show loading overlay
        showLoadingOverlay('Loading courses...');
        startLMSWatchdog();
        
        // Disable course select while loading
        if (tsCourse) {
            tsCourse.clear(true);
            tsCourse.clearOptions();
            tsCourse.setValue('');
            tsCourse.disable();
        } else {
            courseSelect.innerHTML = '<option value="" disabled hidden selected>Select a course...</option>';
            courseSelect.disabled = true;
        }
        
        // Fetch courses for the selected semester
        const url = new URL('/lms/api/lecture-notes/courses', window.location.origin);
        url.searchParams.append('semester', semester);
        
        fetch(url)
            .then(async response => {
                let data;
                try {
                    data = await response.json();
                } catch (err) {
                    throw new Error('University LMS server returned an invalid response.');
                }
                if (!response.ok || !data.success) {
                    throw new Error(data.error || 'Failed to load courses from LMS.');
                }
                return data;
            })
            .then(data => {
                populateCourseDropdown(data.courses || []);
            })
            .catch(error => {
                console.error('Error fetching courses:', error);
                if (tsCourse) {
                    tsCourse.clear(true);
                    tsCourse.clearOptions();
                    tsCourse.addOption({value: '', text: 'Error loading courses'});
                    tsCourse.setValue('');
                } else {
                    courseSelect.innerHTML = '<option value="">Error loading courses</option>';
                }
                showNotification(error.message || 'University LMS server is slow or unreachable.', 'error');
            })
            .finally(() => {
                clearLMSWatchdog();
                if (tsCourse) tsCourse.enable();
                courseSelect.disabled = false;
                hideLoadingOverlay();
            });
    }
    
    function populateCourseDropdown(courses) {
        console.log('Populating course dropdown with', courses.length, 'courses');
        
        if (tsCourse) {
            tsCourse.clear(true);
            tsCourse.clearOptions();
            courses.forEach(course => {
                tsCourse.addOption({value: course.code, text: course.name});
            });
            tsCourse.setValue('');
            tsCourse.sync();
        } else {
            courseSelect.innerHTML = '<option value="" disabled hidden selected>Select a course...</option>';
            courses.forEach(course => {
                const option = document.createElement('option');
                option.value = course.code;
                option.textContent = course.name;
                courseSelect.appendChild(option);
            });
        }
    }
    
    function loadLectureNotes(semester, course, forceRefresh = false) {
        // Hide all states
        hideAllStates();
        
        // Show loading overlay
        showLoadingOverlay('Loading lecture notes...');
        startLMSWatchdog();

        // Spin the refresh button
        if (refreshButton) refreshButton.classList.add('is-refreshing');
        
        // Store current selection
        currentSemester = semester;
        currentCourse = course;

        // If force refresh, use the original blocking fetch endpoint
        if (forceRefresh) {
            const url = new URL('/lms/api/lecture-notes/fetch', window.location.origin);
            if (semester) url.searchParams.append('semester', semester);
            url.searchParams.append('course', course);
            url.searchParams.append('refresh', 'true');

            fetch(url)
                .then(async response => {
                    let data;
                    try {
                        data = await response.json();
                    } catch (err) {
                        throw new Error('University LMS server returned an invalid response.');
                    }
                    if (!response.ok || !data.success) {
                        throw new Error(data.error || 'University LMS server is slow or unreachable right now.');
                    }
                    return data;
                })
                .then(data => {
                    displayLectureNotes(data.lecture_notes || []);
                    showNotification('Lecture notes refreshed successfully', 'success');
                })
                .catch(error => {
                    console.error('Error loading lecture notes:', error);
                    showError(error.message || 'University LMS server timed out or is temporarily offline.');
                })
                .finally(() => {
                    clearLMSWatchdog();
                    hideLoadingOverlay();
                    if (refreshButton) refreshButton.classList.remove('is-refreshing');
                });
            return;
        }

        // Stale-while-revalidate: get cached data immediately, refresh in background
        const url = new URL('/lms/api/lecture-notes/data', window.location.origin);
        if (semester) url.searchParams.append('semester', semester);
        url.searchParams.append('course', course);

        fetch(url)
            .then(async response => {
                let data;
                try {
                    data = await response.json();
                } catch (err) {
                    throw new Error('University LMS server returned an invalid response.');
                }
                if (!response.ok || !data.success) {
                    if (data && (data.survey_required || data.error === 'CMS_SURVEY_REQUIRED')) {
                        if (window.showSurveyModal) window.showSurveyModal();
                    }
                    throw new Error(data.error || 'University LMS server is slow or unreachable right now.');
                }
                return data;
            })
            .then(data => {
                hideLoadingOverlay();
                displayLectureNotes(data.lecture_notes || []);
                if (data.is_stale && data.task_id) {
                    showRefreshingBadge();
                    pollRefreshStatus(data.task_id, 'lecture_notes');
                }
            })
            .catch(error => {
                console.error('Error loading lecture notes:', error);
                hideLoadingOverlay();
                showError(error.message || 'University LMS server timed out or is temporarily offline.');
            })
            .finally(() => {
                clearLMSWatchdog();
                if (refreshButton) refreshButton.classList.remove('is-refreshing');
            });
    }

    function pollRefreshStatus(taskId, dataType, showOverlay = false) {
        const interval = setInterval(() => {
            fetch(`/lms/api/refresh-status?task_id=${taskId}`)
                .then(r => r.json())
                .then(status => {
                    if (!status.done) return;
                    clearInterval(interval);
                    if (showOverlay) hideLoadingOverlay();
                    hideRefreshingBadge();

                    if (status.error) {
                        if (showOverlay) showError(status.error || 'University LMS server did not return data. Please try refreshing.');
                        else showNotification('Background refresh failed', 'error');
                        return;
                    }

                    if (status.data !== null) {
                        if (dataType === 'lecture_notes') displayLectureNotes(status.data);
                        if (!showOverlay) showNotification('Data updated', 'success');
                    }
                })
                .catch(() => clearInterval(interval));
        }, 3000);
    }

    function showRefreshingBadge() {
        let badge = document.getElementById('refreshingBadge');
        if (!badge) {
            badge = document.createElement('div');
            badge.id = 'refreshingBadge';
            badge.style.cssText = 'position:fixed;bottom:20px;right:20px;background:#2563eb;color:#fff;padding:8px 14px;border-radius:20px;font-size:13px;z-index:9000;display:flex;align-items:center;gap:8px;box-shadow:0 2px 8px rgba(0,0,0,0.2)';
            badge.innerHTML = '<span style="display:inline-block;width:10px;height:10px;border:2px solid #fff;border-top-color:transparent;border-radius:50%;animation:spin 0.8s linear infinite"></span> Refreshing...';
            document.body.appendChild(badge);
            if (!document.getElementById('spinStyle')) {
                const s = document.createElement('style');
                s.id = 'spinStyle';
                s.textContent = '@keyframes spin{to{transform:rotate(360deg)}}';
                document.head.appendChild(s);
            }
        }
        badge.style.display = 'flex';
    }

    function hideRefreshingBadge() {
        const badge = document.getElementById('refreshingBadge');
        if (badge) badge.style.display = 'none';
    }
    
    function displayLectureNotes(notes) {
        // Clear table
        tableBody.innerHTML = '';
        
        if (!notes || notes.length === 0) {
            hideAllStates();
            if (downloadAllBtn) downloadAllBtn.style.display = 'none';
            
            emptyState.classList.remove('animate-fade-in-scale');
            void emptyState.offsetWidth;
            emptyState.classList.add('animate-fade-in-scale');
            
            emptyState.style.display = 'flex';
            const title = emptyState.querySelector('.empty-state-title');
            const msg = emptyState.querySelector('.empty-state-message');
            if (title) title.textContent = 'No Lecture Notes Found';
            if (msg) msg.textContent = 'No lecture notes were found for this course.';
            return;
        }
        
        // Populate table with staggered animations
        notes.forEach((note, index) => {
            const row = createLectureNoteRow(note);
            row.classList.add('animate-row');
            row.style.setProperty('--row-idx', index);
            tableBody.appendChild(row);
        });

        updateLectureColumnVisibility();

        // Show Download All button only if there are downloadable files
        const hasFiles = notes.some(n => n.lecture_files && n.lecture_files.length > 0);
        if (downloadAllBtn) downloadAllBtn.style.display = hasFiles ? 'inline-flex' : 'none';
        
        // Show table with fade-in-scale
        lectureNotesContainer.classList.remove('animate-fade-in-scale');
        void lectureNotesContainer.offsetWidth; // Trigger reflow
        lectureNotesContainer.classList.add('animate-fade-in-scale');
        lectureNotesContainer.style.display = 'block';
    }
    
    function updateLectureColumnVisibility() {
        const headers = Array.from(lectureNotesContainer.querySelectorAll('thead th'));
        const rows = Array.from(tableBody.rows);
        const hidden = headers.map((header, index) => rows.length > 0 && rows.every(row => {
            const cell = row.cells[index];
            if (!cell || cell.querySelector('a, button, input, video, audio, iframe')) return false;
            const value = cell.textContent.trim();
            return value === '' || value === '-' || value === '---';
        }));

        headers.forEach((header, index) => {
            header.style.display = hidden[index] ? 'none' : '';
            // Let meaningful columns use the space freed by hidden columns.
            header.style.width = hidden.some(Boolean) ? 'auto' : '';
            rows.forEach(row => {
                if (row.cells[index]) row.cells[index].style.display = hidden[index] ? 'none' : '';
            });
        });
    }

    function createLectureNoteRow(note) {
        const row = document.createElement('tr');
        
        // Week number
        const weekCell = document.createElement('td');
        weekCell.className = 'px-4 py-3 text-center';
        weekCell.textContent = note.week_no || '-';
        row.appendChild(weekCell);
        
        // Lecture title
        const titleCell = document.createElement('td');
        titleCell.className = 'px-4 py-3 font-medium text-start';
        titleCell.textContent = note.title || 'Untitled';
        row.appendChild(titleCell);
        
        // Lecture files
        const filesCell = document.createElement('td');
        filesCell.className = 'px-4 py-3 text-center';
        if (note.lecture_files && note.lecture_files.length > 0) {
            note.lecture_files.forEach((file, index) => {
                const link = document.createElement('a');
                // Use proxy to retain LMS session cookies across the application
                link.href = '/lms/download/' + encodeURIComponent(file) + '?lecture=1';
                link.className = 'subtle-download-btn btn-soft-primary mb-1 me-1';
                link.target = '_blank';
                link.innerHTML = `<i class="fas fa-download"></i> Lecture ${index + 1}`;
                filesCell.appendChild(link);
                if (index < note.lecture_files.length - 1) {
                    filesCell.appendChild(document.createElement('br'));
                }
            });
        } else {
            filesCell.innerHTML = '<span class="text-muted" style="font-size: 0.875rem;">---</span>';
        }
        row.appendChild(filesCell);
        
        // Video links (self recorded)
        const videoCell = document.createElement('td');
        videoCell.className = 'px-4 py-3 text-center';
        if (note.video_links && note.video_links.length > 0) {
            note.video_links.forEach((video, index) => {
                const link = document.createElement('a');
                link.href = video;
                link.className = 'subtle-download-btn btn-soft-secondary mb-1 me-1';
                link.target = '_blank';
                link.innerHTML = `<i class="fas fa-video"></i> Video ${index + 1}`;
                videoCell.appendChild(link);
                if (index < note.video_links.length - 1) {
                    videoCell.appendChild(document.createElement('br'));
                }
            });
        } else {
            videoCell.innerHTML = '<span class="text-muted" style="font-size: 0.875rem;">---</span>';
        }
        row.appendChild(videoCell);
        
        // Supplementary links
        const suppCell = document.createElement('td');
        suppCell.className = 'px-4 py-3 text-center';
        if (note.supplementary_links && note.supplementary_links.length > 0) {
            note.supplementary_links.forEach((link, index) => {
                const a = document.createElement('a');
                a.href = link;
                a.className = 'subtle-download-btn btn-soft-warning mb-1 me-1';
                a.target = '_blank';
                a.innerHTML = `<i class="fas fa-link"></i> Link ${index + 1}`;
                suppCell.appendChild(a);
                if (index < note.supplementary_links.length - 1) {
                    suppCell.appendChild(document.createElement('br'));
                }
            });
        } else {
            suppCell.innerHTML = '<span class="text-muted" style="font-size: 0.875rem;">---</span>';
        }
        row.appendChild(suppCell);
        
        // Remarks
        const remarksCell = document.createElement('td');
        remarksCell.className = 'px-4 py-3 text-center';
        remarksCell.textContent = note.remarks || '-';
        row.appendChild(remarksCell);
        
        return row;
    }
    
    function hideAllStates() {
        const downloadPanel = document.getElementById('lectureDownloadPanel');
        if (downloadPanel) downloadPanel.hidden = true;
        lectureDownloadRequest++;
        lectureDownloadSingle = null;
        const downloadFeedback = document.getElementById('lectureDownloadFeedback');
        if (downloadFeedback) downloadFeedback.textContent = '';
        setLectureDownloadMotion('idle');
        const downloadRecovery = document.getElementById('lectureDownloadRecovery');
        if (downloadRecovery) downloadRecovery.hidden = true;
        if (downloadAllBtn) downloadAllBtn.disabled = false;
        updateRefreshVisibility();
        lectureNotesContainer.style.display = 'none';
        if (downloadAllBtn) downloadAllBtn.style.display = 'none';
        emptyState.style.display = 'none';
        errorState.style.display = 'none';
    }
    
    function showEmptyState() {
        hideAllStates();
        // Reset to default "no course selected" message
        const title = emptyState.querySelector('.empty-state-title');
        const msg = emptyState.querySelector('.empty-state-message');
        if (title) title.textContent = 'No Course Selected';
        if (msg) msg.textContent = 'Select a course from the dropdown above to view lecture notes';
        emptyState.style.display = 'flex';
    }
    
    function showError(message) {
        hideAllStates();
        if (errorMessage) {
            errorMessage.textContent = message || 'University LMS server is slow or unreachable right now.';
        }
        if (errorState) {
            errorState.style.display = 'flex';
        }
    }
    
    // Loading overlay helper
    function showLoadingOverlay(message = 'Loading...') {
        if (loadingOverlay) {
            const loadingText = loadingOverlay.querySelector('p');
            if (loadingText) {
                loadingText.textContent = message;
            }
            loadingOverlay.style.display = 'flex';
        }
    }
    
    function hideLoadingOverlay() {
        if (loadingOverlay) {
            loadingOverlay.style.display = 'none';
        }
    }
    

    // Each request keeps its own target. Never reuse a target that may still be preparing an archive.
    const lectureDownloadOrigin = document.getElementById('lectureNotesScript')?.dataset.downloadOrigin;
    document.addEventListener('visibilitychange', () => {
        const toolbar = document.getElementById('lectureDownloadToolbar');
        toolbar?.setAttribute('data-motion-paused', String(document.hidden));
    });

    function setLectureDownloadMotion(state) {
        const toolbar = document.getElementById('lectureDownloadToolbar');
        if (!toolbar) return;
        if (lectureDownloadResetTimer !== null) {
            clearTimeout(lectureDownloadResetTimer);
            lectureDownloadResetTimer = null;
        }
        toolbar.setAttribute('data-download-state', state);
        toolbar.setAttribute('data-motion-paused', String(document.hidden));
        downloadAllBtn?.setAttribute?.('aria-busy', String(state === 'preparing'));
        const icon = downloadAllBtn?.querySelector?.('i');
        const feedback = document.getElementById('lectureDownloadFeedback');
        const label = document.getElementById('lectureDownloadButtonLabel');
        const announcement = document.getElementById('lectureDownloadAnnouncement');
        const text = state === 'preparing' ? 'Preparing…' : state === 'handoff' ? 'Sent to browser' : 'Download All';
        if (label) label.textContent = text;
        downloadAllBtn?.setAttribute?.('title', state === 'handoff' ? 'Sent to browser. Click again to retry.' : 'Download all lecture files as ZIP');
        if (announcement) announcement.textContent = state === 'handoff' ? 'Sent to browser.' : state === 'preparing' ? 'Preparing download.' : '';
        if (state === 'handoff') {
            // This resets request feedback only; the page cannot observe native download completion.
            lectureDownloadResetTimer = setTimeout(() => {
                lectureDownloadResetTimer = null;
                setLectureDownloadMotion('idle');
            }, 3000);
        }
        // Cancel an interrupted handoff before replaying; never leave an icon between frames.
        for (const target of [icon, feedback, label]) {
            for (const animation of target?.getAnimations?.() || []) animation.cancel();
        }
        if (window.matchMedia?.('(prefers-reduced-motion: reduce)')?.matches) return;
        if (state === 'handoff') {
            icon?.animate?.([
                {transform: 'translateY(0)', opacity: 1},
                {transform: 'translateY(7px)', opacity: 0, offset: .45},
                {transform: 'translateY(-6px)', opacity: 0, offset: .46},
                {transform: 'translateY(0)', opacity: 1}
            ], {duration: 520, easing: 'cubic-bezier(0.16, 1, 0.3, 1)'});
        }
        if (state === 'handoff') {
            label?.animate?.([{opacity: .3}, {opacity: 1}], {duration: 180, easing: 'ease-out'});
        }
        if (state !== 'idle' && feedback?.textContent) {
            feedback.animate?.([{opacity: 0, transform: 'translateX(5px)'}, {opacity: 1, transform: 'translateX(0)'}],
                {duration: 180, easing: 'cubic-bezier(0.16, 1, 0.3, 1)'});
        }
    }

    window.addEventListener('message', event => {
        if (!lectureDownloadOrigin || event.origin !== lectureDownloadOrigin || event.data?.type !== 'lecture-zip-error') return;
        const attempt = [...lectureDownloadFrames.values()].find(item => item.frame.contentWindow === event.source);
        if (attempt && attempt.requestId === lectureDownloadRequest) {
            attempt.status.textContent = String(event.data.error || 'Download failed. Retry.');
            setLectureDownloadMotion('error');
            if (attempt.onError) attempt.onError();
        }
    });

    function submitLectureDownload(instruction, status, onError) {
        const url = new URL(instruction.download_url);
        if (url.origin !== lectureDownloadOrigin ||
            !['/zip', '/download'].includes(url.pathname) || instruction.method !== 'POST') {
            throw new Error('Invalid download destination. Refresh download parts.');
        }
        const frame = document.createElement('iframe');
        frame.name = `lectureDownload_${++lectureDownloadAttempt}`;
        frame.hidden = true;
        frame.title = 'Lecture download request';
        document.body.appendChild(frame);
        lectureDownloadFrames.set(frame.name, {frame, status, onError, requestId: lectureDownloadRequest});
        const form = document.createElement('form');
        form.method = 'POST';
        form.action = url.href;
        form.target = frame.name;
        for (const [name, value] of Object.entries(instruction.form_fields)) {
            const input = document.createElement('input');
            input.type = 'hidden'; input.name = name; input.value = value;
            form.appendChild(input);
        }
        document.body.appendChild(form);
        form.submit();
        form.remove();
        status.textContent = status === document.getElementById('lectureDownloadFeedback') ? '' : 'Sent to browser.';
        setLectureDownloadMotion('handoff');
    }

    async function ensureLectureDownloadProxy() {
        if (!lectureDownloadOrigin) throw new Error('Download service is unavailable.');
        if (lectureDownloadProxyCheck) return lectureDownloadProxyCheck;
        // An invalid token verifies POST support without sending an LMS session or fetching any files.
        lectureDownloadProxyCheck = (async () => {
            const controller = new AbortController();
            const timeout = setTimeout(() => controller.abort(), 10000);
            let response;
            let body;
            try {
                response = await fetch(`${lectureDownloadOrigin}/zip`, {
                    method: 'POST', body: new URLSearchParams({token: 'compatibility-probe'}),
                    redirect: 'error', cache: 'no-store', signal: controller.signal
                });
                body = await response.text();
            } catch (_) {
                throw new Error('Download service unreachable. Retry.');
            } finally { clearTimeout(timeout); }
            // Updated Workers return an HTML error bridge for native POST requests.
            // Older Workers ignore the form body and return plain-text "Missing token".
            if (response.status !== 401 || !response.headers.get('Content-Type')?.includes('text/html') ||
                !body.includes('lecture-zip-error')) {
                throw new Error('Downloads unavailable. Proxy update required.');
            }
        })();
        try { await lectureDownloadProxyCheck; }
        catch (error) {
            lectureDownloadProxyCheck = null;
            throw error;
        }
    }

    async function fetchLectureParts(params) {
        await ensureLectureDownloadProxy();
        const response = await fetch('/lms/api/lecture-notes/download-zip?' + params, {cache: 'no-store'});
        let data;
        try { data = await response.json(); }
        catch (_) { throw new Error('Your session may have expired. Refresh LMS and retry.'); }
        if (!response.ok || !data.success) {
            const message = response.status === 429 ? 'Download request limit reached. Try later or download files individually.' : data.error;
            throw Object.assign(new Error(message || 'Could not prepare downloads. Refresh LMS and retry.'), {code: data.code});
        }
        return data;
    }

    async function downloadAllAsZip(semester, course, partSize = null) {
        if (downloadAllBtn?.disabled) return;
        const panel = document.getElementById('lectureDownloadPanel');
        const feedback = document.getElementById('lectureDownloadFeedback');
        const recovery = document.getElementById('lectureDownloadRecovery');
        const requestId = ++lectureDownloadRequest;
        panel.hidden = true;
        recovery.hidden = true;
        const startSingle = part => {
            submitLectureDownload(part, feedback, () => {
                recovery.hidden = part.count <= 1;
                recovery.onclick = () => downloadAllAsZip(semester, course, Math.ceil(part.count / 2));
            });
        };
        // Reuse prepared instructions for a synchronous, user-initiated retry if the browser blocked the first request.
        if (!partSize && lectureDownloadSingle?.course === course && lectureDownloadSingle?.semester === semester &&
            lectureDownloadSingle.part.expires_at > Date.now() / 1000 + 30) {
            if (downloadAllBtn) downloadAllBtn.disabled = true;
            try { startSingle(lectureDownloadSingle.part); }
            catch (error) { feedback.textContent = error.message; setLectureDownloadMotion('error'); }
            finally { setTimeout(() => { if (requestId === lectureDownloadRequest && downloadAllBtn) downloadAllBtn.disabled = false; }, 1500); }
            return;
        }
        lectureDownloadSingle = null;
        feedback.textContent = '';
        setLectureDownloadMotion('preparing');
        const list = document.getElementById('lectureDownloadParts');
        list.replaceChildren();
        if (downloadAllBtn) downloadAllBtn.disabled = true;
        const params = new URLSearchParams({course});
        if (semester) params.set('semester', semester);
        if (partSize) params.set('part_size', partSize);
        const name = courseSelect?.options[courseSelect.selectedIndex]?.text;
        if (name) params.set('course_name', name);
        document.getElementById('lectureDownloadClose').onclick = () => {
            panel.hidden = true;
            if (downloadAllBtn) downloadAllBtn.focus();
        };
        const review = document.getElementById('lectureDownloadReview');
        review.hidden = true;
        review.onclick = () => downloadAllAsZip(semester, course);
        recovery.onclick = () => downloadAllAsZip(semester, course, partSize);
        try {
            const data = await fetchLectureParts(params);
            if (requestId !== lectureDownloadRequest) return;
            if (!data.parts?.length) throw new Error('No lecture files available.');
            if (data.parts.length === 1) {
                const part = data.parts[0];
                lectureDownloadSingle = {course, semester, part};
                if (part.expires_at <= Date.now() / 1000 + 30) {
                    feedback.textContent = 'Download link expired. Retry.';
                    setLectureDownloadMotion('error');
                    return;
                }
                startSingle(part);
                return;
            }
            feedback.textContent = '';
            setLectureDownloadMotion('idle');
            document.getElementById('lectureDownloadHeading').textContent = `${data.parts.length} ZIP files`;
            document.getElementById('lectureDownloadStatus').textContent = `${data.total_files} lecture files`;
            const context = {params, manifest: data.manifest, requestId, review};
            data.parts.forEach((part, index) => list.appendChild(renderLecturePart(part, context, `Part ${index + 1}`)));
            panel.hidden = false;
            document.getElementById('lectureDownloadHeading').focus();
        } catch (error) {
            if (requestId !== lectureDownloadRequest) return;
            feedback.textContent = error.message;
            setLectureDownloadMotion('error');
        } finally {
            if (requestId === lectureDownloadRequest && downloadAllBtn) downloadAllBtn.disabled = false;
        }
    }

    function lectureWeekLabel(weeks) {
        const sorted = [...new Set(weeks)].sort((a, b) => a - b);
        if (!sorted.length) return '';
        const ranges = [];
        for (let i = 0; i < sorted.length; i++) {
            const first = sorted[i];
            while (i + 1 < sorted.length && sorted[i + 1] === sorted[i] + 1) i++;
            ranges.push(first === sorted[i] ? String(first) : `${first}–${sorted[i]}`);
        }
        return `${sorted.length === 1 ? 'Week' : 'Weeks'} ${ranges.join(', ')}`;
    }

    function renderLecturePart(part, context, label) {
        const row = document.createElement('li');
        row.className = 'lecture-download-part';
        const title = document.createElement('h4');
        title.textContent = label;
        const description = document.createElement('p');
        const weeks = lectureWeekLabel(part.weeks);
        description.textContent = `${part.count} file${part.count === 1 ? '' : 's'}${weeks ? ' · ' + weeks : ''}`;
        const status = document.createElement('p');
        status.className = 'lecture-part-status';
        status.setAttribute('role', 'status');
        const actions = document.createElement('div');
        actions.className = 'lecture-download-actions';
        const button = (text, primary = false) => {
            const node = document.createElement('button');
            node.type = 'button'; node.className = `lecture-download-button${primary ? ' lecture-download-primary' : ''}`;
            node.textContent = text;
            return node;
        };
        const download = button('Download', true);
        download.setAttribute('aria-label', `Download ${label.toLowerCase()}`);
        const smaller = button('Smaller parts');
        smaller.hidden = part.count === 1;
        const details = document.createElement('details');
        const summary = document.createElement('summary');
        summary.textContent = 'Files & options';
        details.appendChild(summary);
        const archiveName = document.createElement('p');
        archiveName.className = 'lecture-archive-name';
        archiveName.textContent = part.filename;
        details.appendChild(archiveName);
        const fileList = document.createElement('ul');
        fileList.className = 'lecture-download-files';
        details.appendChild(fileList);
        const prepare = async (limit) => {
            const query = new URLSearchParams(context.params);
            query.set('manifest', context.manifest);
            query.set('start', part.start); query.set('end', part.end);
            query.set('part_size', limit);
            row.setAttribute('aria-busy', 'true');
            row.querySelectorAll('button').forEach(node => { node.disabled = true; });
            status.textContent = 'Preparing…';
            try {
                const data = await fetchLectureParts(query);
                if (context.requestId !== lectureDownloadRequest) return;
                const rows = data.parts.map(item => renderLecturePart(item, context, `Files ${item.start + 1}–${item.end}`));
                row.replaceWith(...rows);
                rows[0]?.querySelector('button')?.focus();
                // Explicit second click keeps the browser download tied to a user action.
                rows[0]?.querySelector('.lecture-part-status')?.replaceChildren(document.createTextNode('Ready to download.'));
            } catch (error) {
                if (context.requestId !== lectureDownloadRequest) return;
                status.textContent = error.message;
                if (error.code === 'manifest_changed') {
                    context.review.textContent = 'Review updated parts';
                    context.review.hidden = false;
                }
            } finally {
                row.setAttribute('aria-busy', 'false');
                row.querySelectorAll('button').forEach(node => { node.disabled = false; });
            }
        };
        const start = (instruction, control) => {
            if (Date.now() / 1000 >= instruction.expires_at - 30) {
                void prepare(Math.min(20, part.count));
                return;
            }
            control.disabled = true;
            try {
                submitLectureDownload(instruction, status);
                control.textContent = 'Retry';
            } catch (error) { status.textContent = error.message; }
            // Prevent accidental double-clicks; this timer never claims transfer completion.
            setTimeout(() => { control.disabled = false; }, 1500);
        };
        download.onclick = () => start(part, download);
        smaller.onclick = () => { void prepare(Math.ceil(part.count / 2)); };
        for (const file of part.files) {
            const item = document.createElement('li');
            const name = document.createElement('span');
            name.textContent = file.name;
            const individual = button('Download file');
            individual.setAttribute('aria-label', `Download ${file.name}`);
            individual.onclick = () => start(file, individual);
            item.append(name, individual);
            fileList.appendChild(item);
        }
        const info = document.createElement('div');
        info.className = 'lecture-download-info';
        info.append(title, description);
        actions.append(download);
        details.appendChild(smaller);
        row.append(info, actions, status, details);
        return row;
    }

    // Notification helper
    let notificationTimeout = null;

    function showNotification(message, type = 'success', title = null) {
        let banner = document.getElementById('notificationBanner');
        if (!banner) {
            banner = document.createElement('div');
            banner.id = 'notificationBanner';
            banner.className = 'notification-banner';
            document.body.appendChild(banner);
        } else if (banner.parentElement !== document.body) {
            document.body.appendChild(banner);
        }

        if (notificationTimeout) {
            clearTimeout(notificationTimeout);
            notificationTimeout = null;
        }

        const icons = {
            success: 'fa-check',
            error: 'fa-circle-exclamation',
            warning: 'fa-triangle-exclamation',
            info: 'fa-circle-info'
        };
        const iconClass = icons[type] || icons.success;
        const toastTypeClass = `toast-${type}`;

        let textHtml = '';
        if (title) {
            textHtml = `
                <div class="toast-text-wrap">
                    <div class="toast-title">${title}</div>
                    <div class="toast-message">${message}</div>
                </div>
            `;
        } else {
            textHtml = `
                <div class="toast-text-wrap">
                    <div class="toast-title" style="font-weight: 500;">${message}</div>
                </div>
            `;
        }

        banner.innerHTML = `
            <div class="notification-toast ${toastTypeClass}" role="status" aria-live="polite">
                <div class="toast-icon-wrap">
                    <i class="fas ${iconClass}"></i>
                </div>
                ${textHtml}
                <button type="button" class="toast-close-btn" aria-label="Close notification">
                    <i class="fas fa-xmark"></i>
                </button>
            </div>
        `;

        banner.style.display = 'block';

        const toastEl = banner.querySelector('.notification-toast');
        const closeBtn = banner.querySelector('.toast-close-btn');

        function dismissToast() {
            if (!toastEl) return;
            toastEl.style.animation = 'toastExitToTop 0.15s cubic-bezier(0.16, 1, 0.3, 1) forwards';
            setTimeout(() => {
                banner.style.display = 'none';
                banner.innerHTML = '';
            }, 140);
        }

        if (closeBtn) {
            closeBtn.addEventListener('click', dismissToast);
        }

        // Auto-hide with hover pause (3.5 seconds)
        let remaining = 3500;
        let start = Date.now();

        function scheduleHide() {
            notificationTimeout = setTimeout(() => {
                dismissToast();
            }, remaining);
        }

        if (toastEl) {
            toastEl.addEventListener('mouseenter', () => {
                clearTimeout(notificationTimeout);
                remaining -= (Date.now() - start);
            });
            toastEl.addEventListener('mouseleave', () => {
                start = Date.now();
                scheduleHide();
            });
        }

        scheduleHide();
    }

    window.showNotification = showNotification;
});
