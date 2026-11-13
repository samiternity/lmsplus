// LMS Assignments - New Implementation
// Mimics the original LMS behavior with dropdown selection

console.log('LMS Assignments script loaded');
const ASSIGNMENT_UPLOAD_MAX_SIZE = 15 * 1024 * 1024;

document.addEventListener('DOMContentLoaded', function () {
        function formatDeadline(deadlineStr) {
        if (!deadlineStr) return '';
        const match = deadlineStr.match(/^(\d{1,2})\s+([a-zA-Z]+)\s+(\d{4})-(\d{1,2}:\d{2})\s+([a-zA-Z]{2})$/);
        if (match) {
            const day = match[1].padStart(2, '0');
            const month = match[2].charAt(0).toUpperCase() + match[2].slice(1, 3).toLowerCase(); // 3-letter month
            const year = match[3];
            const time = match[4];
            const ampm = match[5].toUpperCase();
            return `<div style="display: flex; align-items: center; justify-content: center; gap: 8px; font-variant-numeric: tabular-nums; white-space: nowrap;">
                <span style="display: flex; align-items: center; gap: 4px;"><i class="far fa-calendar-alt" style="opacity: 0.7;"></i> <span>${day} ${month} ${year}</span></span>
                <span style="opacity: 0.3;">|</span>
                <span style="display: flex; align-items: center; gap: 4px;"><i class="far fa-clock" style="opacity: 0.7;"></i> <span>${time} ${ampm}</span></span>
            </div>`;
        }
        return deadlineStr;
    }

    console.log('DOM Content Loaded - Initializing assignments');

    // DOM Elements
    const semesterSelect = document.getElementById('semesterSelect');
    const courseSelect = document.getElementById('courseSelect');
    
    // TomSelect Instances
    let tsSemester = null;
    let tsCourse = null;

    const refreshButton = document.getElementById('refreshAssignments');
    const loadingOverlay = document.getElementById('loadingOverlay');
    const notificationBanner = document.getElementById('notificationBanner');
    const assignmentsContainer = document.getElementById('assignmentsContainer');
    const emptyState = document.getElementById('emptyState');
    const errorState = document.getElementById('errorState');
    const errorMessage = document.getElementById('errorMessage');
    const tableBody = document.getElementById('assignmentsTableBody');

    console.log('Elements found:', {
        semesterSelect: !!semesterSelect,
        courseSelect: !!courseSelect,
        refreshButton: !!refreshButton
    });

    // State
    let currentSemester = null;
    let currentCourse = null;

    // Initialize
    init();

    function init() {
        console.log('Initializing assignments interface');

        // Show empty state initially
        showEmptyState();

        // Initialize TomSelect if available
        if (typeof TomSelect !== 'undefined') {
            const tsOptions = { 
                create: false, 
                controlInput: null, // Disables the blinking cursor/text input
                maxOptions: null,
                dropdownParent: 'body' // Prevents dropdown from being clipped by overflow:hidden
            };
            
            // Do NOT use sortField, we must preserve backend sorting order!
            if (semesterSelect) tsSemester = new TomSelect(semesterSelect, tsOptions);
            if (courseSelect) tsCourse = new TomSelect(courseSelect, {...tsOptions, placeholder: 'Select a course...', allowEmptyOption: false});
        }

        // Handle semester change - update course dropdown
        if (semesterSelect) {
            semesterSelect.addEventListener('change', function () {
                console.log('Semester changed:', this.value);
                const semester = this.value;

                if (tsSemester) {
                    tsSemester.close();
                    tsSemester.blur();
                }

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

        // Auto-load assignments when course is selected
        if (courseSelect) {
            courseSelect.addEventListener('change', function () {
                console.log('Course selected:', this.value);
                const semester = semesterSelect ? semesterSelect.value : null;
                const course = this.value;

                if (tsCourse) {
                    tsCourse.close();
                    tsCourse.blur();
                }

                if (semester && course) {
                    // Auto-load assignments
                    loadAssignments(semester, course);
                } else {
                    // Show empty state if no course selected
                    showEmptyState();
                }
            });
        } else {
            console.error('Load button not found!');
        }

        // Refresh button
        if (refreshButton) {
            refreshButton.addEventListener('click', function () {
                if (semesterSelect.value && courseSelect.value) {
                    loadAssignments(semesterSelect.value, courseSelect.value, true);
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

        // Disable course select while loading
        if (tsCourse) {
            tsCourse.disable();
            tsCourse.clear(true);
            tsCourse.clearOptions();
            tsCourse.setValue('');
            tsCourse.refreshOptions(false);
        } else {
            courseSelect.disabled = true;
            courseSelect.innerHTML = '<option value="" disabled hidden selected>Select a course...</option>';
        }

        // Fetch courses for the selected semester
        const url = `/lms/api/assignments/courses?semester=${encodeURIComponent(semester)}`;

        fetch(url)
            .then(response => response.json())
            .then(data => {
                if (data.success) {
                    populateCourseDropdown(data.courses);
                } else {
                    console.error('Failed to load courses:', data.error);
                    if (tsCourse) {
                        tsCourse.clear(true);
                        tsCourse.clearOptions();
                        tsCourse.addOption({value: '', text: 'Error loading courses'});
                        tsCourse.setValue('');
                    } else {
                        courseSelect.innerHTML = '<option value="">Error loading courses</option>';
                    }
                    showNotification('Failed to load courses', 'error');
                }
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
                showNotification('Network error loading courses', 'error');
            })
            .finally(() => {
                if (tsCourse) {
                    tsCourse.enable();
                } else {
                    courseSelect.disabled = false;
                }
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
            tsCourse.clear(true); // Show the placeholder without adding a selectable option
            tsCourse.refreshOptions(false);
        } else {
            // Fallback for native select
            courseSelect.innerHTML = '<option value="" disabled hidden selected>Select a course...</option>';
            courses.forEach(course => {
                const option = document.createElement('option');
                option.value = course.code;
                option.textContent = course.name;
                courseSelect.appendChild(option);
            });
        }
    }

    function loadAssignments(semester, course, forceRefresh = false) {
        // Hide all states
        hideAllStates();

        // Show loading overlay
        showLoadingOverlay('Loading assignments...');
        if (refreshButton) refreshButton.classList.add('is-refreshing');
        
        currentSemester = semester;
        currentCourse = course;

        // If force refresh, use the original blocking fetch endpoint
        if (forceRefresh) {
            let url = `/lms/api/assignments/fetch?course=${encodeURIComponent(course)}&refresh=true`;
            if (semester) url += `&semester=${encodeURIComponent(semester)}`;

            fetch(url)
                .then(response => response.json())
                .then(data => {
                    if (data.success) {
                        displayAssignments(data.assignments);
                        showNotification('Assignments refreshed successfully', 'success');
                    } else {
                        showError(data.error || 'Failed to load assignments');
                    }
                })
                .catch(error => {
                    console.error('Error loading assignments:', error);
                    showError('Network error. Please check your connection and try again.');
                })
                .finally(() => hideLoadingOverlay());
            return;
        }

        // Stale-while-revalidate: get cached data immediately, refresh in background
        let url = `/lms/api/assignments/data?course=${encodeURIComponent(course)}`;
        if (semester) url += `&semester=${encodeURIComponent(semester)}`;

        fetch(url)
            .then(response => response.json())
            .then(data => {
                hideLoadingOverlay();
                if (!data.success) {
                    if (data && (data.survey_required || data.error === 'CMS_SURVEY_REQUIRED')) {
                        if (window.showSurveyModal) window.showSurveyModal();
                    }
                    showError(data.error || 'Failed to load assignments');
                    return;
                }

                displayAssignments(data.assignments || []);
                if (data.is_stale && data.task_id) {
                    showRefreshingBadge('assignments');
                    pollRefreshStatus(data.task_id, 'assignments');
                }
            })
            .catch(error => {
                console.error('Error loading assignments:', error);
                hideLoadingOverlay();
                showError('Network error. Please check your connection and try again.');
            });
    }

    function pollRefreshStatus(taskId, dataType, showOverlay = false) {
        const interval = setInterval(() => {
            fetch(`/lms/api/refresh-status?task_id=${taskId}`)
                .then(r => r.json())
                .then(status => {
                    if (!status.done) return; // keep polling
                    clearInterval(interval);
                    if (showOverlay) hideLoadingOverlay();
                    hideRefreshingBadge();

                    if (status.error) {
                        if (showOverlay) showError('Failed to load data from LMS. Please try refreshing.');
                        else showNotification('Background refresh failed', 'error');
                        return;
                    }

                    if (status.data !== null) {
                        if (dataType === 'assignments') displayAssignments(status.data);
                        else displayLectureNotes(status.data);
                        if (!showOverlay) showNotification('Data updated', 'success');
                    }
                })
                .catch(() => clearInterval(interval));
        }, 3000);
    }

    function showRefreshingBadge(type) {
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

    function displayAssignments(assignments) {
        // Clear table
        tableBody.innerHTML = '';

        const colRemarks = document.getElementById('col-remarks');
        if (colRemarks) colRemarks.style.display = 'none';

        if (!assignments || assignments.length === 0) {
            // Show empty state with a more appropriate message
            hideAllStates();
            
            // Trigger animation reflow
            emptyState.classList.remove('animate-fade-in-scale');
            void emptyState.offsetWidth;
            emptyState.classList.add('animate-fade-in-scale');
            
            emptyState.style.display = 'flex';
            const title = emptyState.querySelector('.empty-state-title');
            const msg = emptyState.querySelector('.empty-state-message');
            if (title) title.textContent = 'No Assignments Found';
            if (msg) msg.textContent = 'No assignments were found for this course.';
            return;
        }

        // Determine dynamic columns based on data
        const showRemarks = assignments.some(a => getAssignmentRemarks(a));
        const showMarks = assignments.some(a => {
            const m = a.marks_obtained !== null && a.marks_obtained !== undefined ? String(a.marks_obtained).trim().toLowerCase() : '';
            return m && !['not marked yet', 'no marks obtained', 'no marks', '-', '---', 'none', 'n/a'].includes(m);
        });

        const showFeedback = assignments.some(a => {
            const c = a.comments ? String(a.comments).trim().toLowerCase() : '';
            const hasComments = c && !['none', 'no comments', 'no comments.', '-', '---', 'n/a', 'null'].includes(c);
            return hasComments || !!a.returned_file;
        });

        // Toggle Table Headers
        const colMarks = document.getElementById('col-marks');
        const colFeedback = document.getElementById('col-feedback');
        if (colRemarks) colRemarks.style.display = showRemarks ? '' : 'none';
        if (colMarks) colMarks.style.display = showMarks ? '' : 'none';
        if (colFeedback) colFeedback.style.display = showFeedback ? '' : 'none';

        // Populate table with staggered animations
        assignments.forEach((assignment, index) => {
            const row = createAssignmentRow(assignment, showRemarks, showMarks, showFeedback);
            // Setup staggered animation
            row.classList.add('animate-row');
            row.style.setProperty('--row-idx', index);
            tableBody.appendChild(row);
        });

        // Show table with fade-in-scale
        assignmentsContainer.classList.remove('animate-fade-in-scale');
        void assignmentsContainer.offsetWidth; // Trigger reflow
        assignmentsContainer.classList.add('animate-fade-in-scale');
        assignmentsContainer.style.display = 'block';
    }

    function getAssignmentRemarks(assignment) {
        const remarks = assignment.remarks == null ? '' : String(assignment.remarks).trim();
        return remarks && !['none', 'no remarks', 'no remarks.', '-', '---', 'n/a', 'null'].includes(remarks.toLowerCase())
            ? remarks : '';
    }

    function createAssignmentRow(assignment, showRemarks = false, showMarks = true, showFeedback = true) {
        const row = document.createElement('tr');
        
        const groupKeywords = ['group', 'project', 'ccp', 'pbl', 'problem based learning', 'presentation', 'assignment 3', 'assignment#3', 'proposal'];
        const titleLower = (assignment.title || '').toLowerCase();
        const isPotentialGroup = groupKeywords.some(kw => titleLower.includes(kw));

        // DEBUG: Log each assignment row
        console.log(`\n=== Row ${assignment.assignment_no}: ${assignment.title} ===`);
        console.log('  assignment_file:', assignment.assignment_file ? 'YES' : 'NO');
        console.log('  solution_file:', assignment.solution_file ? 'YES' : 'NO');
        console.log('  submission_file:', assignment.submission_file ? 'YES' : 'NO');
        if (assignment.solution_file) {
            console.log('  solution_file URL:', assignment.solution_file);
        }

        // Title with potential group icon
        const titleCell = document.createElement('td');
        titleCell.className = 'px-4 py-3 font-medium text-start';
        
        let titleHtml = escapeHtml(assignment.title || '');
        if (isPotentialGroup && typeof GROUPS_ENABLED !== 'undefined' && GROUPS_ENABLED) {
            titleHtml += ' <i class="fas fa-users text-primary ms-2" title="Group Assignment" style="font-size: 0.875em; opacity: 0.8;"></i>';
        }
        
        titleCell.innerHTML = titleHtml;

        // No.
        const noCell = document.createElement('td');
        noCell.className = 'px-4 py-3 text-center';
        noCell.textContent = assignment.assignment_no;
        row.appendChild(noCell);
        row.appendChild(titleCell);

        if (showRemarks) {
            const remarksCell = document.createElement('td');
            remarksCell.className = 'px-4 py-3 text-start assignment-remarks-cell';
            const remarks = getAssignmentRemarks(assignment);
            remarksCell.textContent = remarks ? `“${remarks.replace(/^["'“”]+|["'“”]+$/g, '').trim()}”` : '—';
            row.appendChild(remarksCell);
        }

        // Files (Assignment + Solution)
        const filesCell = document.createElement('td');
        filesCell.className = 'px-4 py-3 text-center';

        if (assignment.assignment_file) {
            const assignmentLink = document.createElement('a');
            assignmentLink.href = '/lms/download/' + encodeURIComponent(assignment.assignment_file);
            assignmentLink.className = 'subtle-download-btn btn-soft-primary mb-1 me-1';
            assignmentLink.target = '_blank';
            assignmentLink.innerHTML = '<i class="fas fa-file-download"></i> Assignment';
            filesCell.appendChild(assignmentLink);
        }

        if (assignment.solution_file) {
            const solutionLink = document.createElement('a');
            solutionLink.href = '/lms/download/' + encodeURIComponent(assignment.solution_file);
            solutionLink.className = 'subtle-download-btn btn-soft-primary mb-1 me-1';
            solutionLink.target = '_blank';
            solutionLink.innerHTML = '<i class="fas fa-file-download"></i> Solution';
            filesCell.appendChild(solutionLink);
        }

        row.appendChild(filesCell);

        // Submission
        const submissionCell = document.createElement('td');
        submissionCell.className = 'px-4 py-3 text-center';

        if (assignment.submission_file) {
            const submissionLink = document.createElement('a');
            submissionLink.href = '/lms/download/' + encodeURIComponent(assignment.submission_file);
            submissionLink.className = 'subtle-download-btn btn-soft-secondary mb-1';
            submissionLink.target = '_blank';
            submissionLink.innerHTML = '<i class="fas fa-file-export"></i> Submission';
            submissionCell.appendChild(submissionLink);
        } else {
            const noSubSpan = document.createElement('span');
            noSubSpan.className = 'text-muted';
            noSubSpan.style.fontSize = '0.875rem';
            noSubSpan.textContent = 'No Submission';
            submissionCell.appendChild(noSubSpan);
        }

        row.appendChild(submissionCell);

        // Marks obtained
        if (showMarks) {
            const marksCell = document.createElement('td');
            marksCell.className = 'px-4 py-3 text-center';

            const rawMarks = assignment.marks_obtained;
            const marksLower = rawMarks !== null && rawMarks !== undefined ? String(rawMarks).trim().toLowerCase() : '';
            const hasNoMarks = !marksLower || ['not marked yet', 'no marks obtained', 'no marks', '-', 'none', 'n/a'].includes(marksLower);

            if (hasNoMarks) {
                marksCell.innerHTML = '<span class="text-muted" style="font-size: 0.875rem;">---</span>';
            } else {
                marksCell.textContent = rawMarks;
            }

            row.appendChild(marksCell);
        }

        // Returned submission / comments
        if (showFeedback) {
            const returnedCell = document.createElement('td');
            returnedCell.className = 'px-4 py-3 text-center';

            const commentsLower = assignment.comments ? String(assignment.comments).trim().toLowerCase() : '';
            const hasComments = commentsLower && !['none', 'no comments', 'no comments.', '-', 'n/a', 'null'].includes(commentsLower);

            if (assignment.returned_file) {
                console.log('Returned file URL:', assignment.returned_file);
                const returnedLink = document.createElement('a');
                returnedLink.href = '/lms/download/' + encodeURIComponent(assignment.returned_file);
                returnedLink.className = 'subtle-download-btn btn-soft-warning mb-1 me-1';
                returnedLink.target = '_blank';
                returnedLink.innerHTML = '<i class="fas fa-file-invoice"></i> Returned File';
                returnedCell.appendChild(returnedLink);
            }

                                                            if (hasComments) {
                const commentsDiv = document.createElement('div');
                commentsDiv.style.display = 'inline-block';
                commentsDiv.style.textAlign = 'center';
                commentsDiv.style.fontSize = '0.8125rem';
                commentsDiv.style.color = 'var(--text-secondary)';
                commentsDiv.style.lineHeight = '1.4';
                commentsDiv.style.fontStyle = 'italic';
                commentsDiv.style.maxWidth = '250px';
                commentsDiv.style.wordBreak = 'break-word';
                
                // Wrap in smart quotes to denote feedback without needing an icon
                const cleanComment = assignment.comments.replace(/^["']|["']$/g, '').trim();
                commentsDiv.textContent = `"${cleanComment}"`;
                
                commentsDiv.title = assignment.comments;
                returnedCell.appendChild(commentsDiv);
            }

            if (!assignment.returned_file && !hasComments) {
                const noRetSpan = document.createElement('span');
                noRetSpan.className = 'text-muted';
                noRetSpan.style.fontSize = '0.875rem';
                noRetSpan.textContent = '---';
                returnedCell.appendChild(noRetSpan);
            }

            row.appendChild(returnedCell);
        }

        // Action
        const actionCell = document.createElement('td');
        actionCell.className = 'px-4 py-3 text-center assignment-action-cell';
        actionCell.style.verticalAlign = 'middle';

        console.log('Action column for', assignment.title, '- can_submit:', assignment.can_submit, 'is_overdue:', assignment.is_overdue, 'status:', assignment.submission_status, 'has_submission:', !!assignment.submission_file);

        if (assignment.can_submit) {
            // Check if there's already a submission
            if (assignment.submission_file) {
                // Show only DELETE button (yellow background like LMS)
                const deleteBtn = document.createElement('div');
                deleteBtn.style.background = '#e6c300';
                deleteBtn.style.color = '#fff';
                deleteBtn.style.display = 'flex';
                deleteBtn.style.alignItems = 'center';
                deleteBtn.style.justifyContent = 'center';
                deleteBtn.style.textAlign = 'center';
                deleteBtn.style.lineHeight = '22px';
                deleteBtn.style.padding = '11px 0px';
                deleteBtn.style.borderRadius = '5px';
                deleteBtn.style.cursor = 'pointer';
                deleteBtn.style.fontSize = '12px';
                deleteBtn.innerHTML = '<i class="fa fa-trash" style="margin-right: 8px;"></i>Delete';
                deleteBtn.onclick = () => showDeleteModal(assignment);
                actionCell.appendChild(deleteBtn);
                
                // --- GROUP DELETE BUTTON ---
                if (typeof GROUPS_ENABLED !== 'undefined' && GROUPS_ENABLED) {
                    const groupDeleteBtn = document.createElement('div');
                    groupDeleteBtn.style.background = '#dc3545';
                    groupDeleteBtn.style.color = '#fff';
                    groupDeleteBtn.style.display = 'flex';
                    groupDeleteBtn.style.alignItems = 'center';
                    groupDeleteBtn.style.justifyContent = 'center';
                    groupDeleteBtn.style.textAlign = 'center';
                    groupDeleteBtn.style.lineHeight = '22px';
                    groupDeleteBtn.style.padding = '11px 0px';
                    groupDeleteBtn.style.borderRadius = '5px';
                    groupDeleteBtn.style.cursor = 'pointer';
                    groupDeleteBtn.style.fontSize = '14px';
                    groupDeleteBtn.style.marginTop = '5px';
                    groupDeleteBtn.title = 'Group Delete';
                    groupDeleteBtn.innerHTML = '<i class="fas fa-users-slash"></i>';
                    groupDeleteBtn.onclick = () => window.showGroupUploadModal(assignment); // We reuse the Group Upload Modal which has an Audit Tab!
                    actionCell.appendChild(groupDeleteBtn);
                }
                // --------------------------
            } else {
                // Show only SUBMIT button (green background like LMS)
                const submitBtn = document.createElement('button');
                submitBtn.type = 'button';
                submitBtn.className = 'assignment-submit-btn';
                submitBtn.innerHTML = '<i class="fa fa-plus-square" aria-hidden="true"></i><span>Submit</span>';
                submitBtn.onclick = () => showUploadModal(assignment);
                actionCell.appendChild(submitBtn);

                // --- GROUP UPLOAD BUTTON ---
                if (typeof GROUPS_ENABLED !== 'undefined' && GROUPS_ENABLED) {
                    const groupUploadBtn = document.createElement('div');
                    groupUploadBtn.style.background = isPotentialGroup ? '#ff9800' : '#17a2b8';
                    groupUploadBtn.style.color = '#fff';
                    groupUploadBtn.style.display = 'flex';
                    groupUploadBtn.style.alignItems = 'center';
                    groupUploadBtn.style.justifyContent = 'center';
                    groupUploadBtn.style.textAlign = 'center';
                    groupUploadBtn.style.lineHeight = '22px';
                    groupUploadBtn.style.padding = '11px 0px';
                    groupUploadBtn.style.borderRadius = '5px';
                    groupUploadBtn.style.cursor = 'pointer';
                    groupUploadBtn.style.fontSize = '14px';
                    groupUploadBtn.style.marginTop = '5px';
                    
                    if (isPotentialGroup) {
                        groupUploadBtn.style.boxShadow = '0 0 8px rgba(255, 152, 0, 0.8)';
                        groupUploadBtn.title = 'Possible group assignment';
                    } else {
                        groupUploadBtn.title = 'Group Upload';
                    }
                    
                    groupUploadBtn.innerHTML = '<i class="fas fa-users"></i>';
                    groupUploadBtn.onclick = () => window.showGroupUploadModal(assignment);
                    actionCell.appendChild(groupUploadBtn);
                }
                // --------------------------
            }
                } else if (assignment.is_overdue) {
            if (assignment.submission_file) {
                const submittedDiv = document.createElement('div');
                submittedDiv.style.color = 'var(--success-color)';
                submittedDiv.style.textAlign = 'center';
                submittedDiv.style.padding = '4px 0px';
                submittedDiv.style.fontSize = '0.8125rem';
                submittedDiv.style.fontWeight = '600';
                submittedDiv.innerHTML = '<i class="fas fa-check"></i> Complete';
                actionCell.appendChild(submittedDiv);
            } else {
                const overdueDiv = document.createElement('div');
                overdueDiv.style.color = 'var(--error-color)';
                overdueDiv.style.textAlign = 'center';
                overdueDiv.style.padding = '4px 0px';
                overdueDiv.style.fontSize = '0.8125rem';
                overdueDiv.style.fontWeight = '600';
                overdueDiv.innerHTML = '<i class="fas fa-exclamation-circle"></i> Deadline Exceeded';
                actionCell.appendChild(overdueDiv);
            }

            // --- GROUP UPLOAD BUTTON ---
            if (typeof GROUPS_ENABLED !== 'undefined' && GROUPS_ENABLED) {
                if (!assignment.submission_file) {
                    const groupUploadBtn = document.createElement('div');
                    groupUploadBtn.style.background = isPotentialGroup ? '#ff9800' : '#17a2b8';
                    groupUploadBtn.style.color = '#fff';
                    groupUploadBtn.style.display = 'flex';
                    groupUploadBtn.style.alignItems = 'center';
                    groupUploadBtn.style.justifyContent = 'center';
                    groupUploadBtn.style.textAlign = 'center';
                    groupUploadBtn.style.lineHeight = '22px';
                    groupUploadBtn.style.padding = '11px 0px';
                    groupUploadBtn.style.borderRadius = '5px';
                    groupUploadBtn.style.cursor = 'pointer';
                    groupUploadBtn.style.fontSize = '14px';
                    groupUploadBtn.style.marginTop = '5px';
                    
                    if (isPotentialGroup) {
                        groupUploadBtn.style.boxShadow = '0 0 8px rgba(255, 152, 0, 0.8)';
                        groupUploadBtn.title = 'Possible group assignment';
                    } else {
                        groupUploadBtn.title = 'Group Upload';
                    }
                    
                    groupUploadBtn.innerHTML = '<i class="fas fa-users"></i>';
                    groupUploadBtn.onclick = () => window.showGroupUploadModal(assignment);
                    actionCell.appendChild(groupUploadBtn);
                }
            }
            // --------------------------
        } else {
            actionCell.textContent = '-';
            // --- GROUP UPLOAD BUTTON ---
            if (typeof GROUPS_ENABLED !== 'undefined' && GROUPS_ENABLED) {
                if (!assignment.submission_file) {
                    const groupUploadBtn = document.createElement('div');
                    groupUploadBtn.style.background = isPotentialGroup ? '#ff9800' : '#17a2b8';
                    groupUploadBtn.style.color = '#fff';
                    groupUploadBtn.style.display = 'flex';
                    groupUploadBtn.style.alignItems = 'center';
                    groupUploadBtn.style.justifyContent = 'center';
                    groupUploadBtn.style.textAlign = 'center';
                    groupUploadBtn.style.lineHeight = '22px';
                    groupUploadBtn.style.padding = '11px 0px';
                    groupUploadBtn.style.borderRadius = '5px';
                    groupUploadBtn.style.cursor = 'pointer';
                    groupUploadBtn.style.fontSize = '14px';
                    groupUploadBtn.style.marginTop = '5px';
                    
                    if (isPotentialGroup) {
                        groupUploadBtn.style.boxShadow = '0 0 8px rgba(255, 152, 0, 0.8)';
                        groupUploadBtn.title = 'Possible group assignment';
                    } else {
                        groupUploadBtn.title = 'Group Upload';
                    }
                    
                    groupUploadBtn.innerHTML = '<i class="fas fa-users"></i>';
                    groupUploadBtn.onclick = () => window.showGroupUploadModal(assignment);
                    actionCell.appendChild(groupUploadBtn);
                }
            }
            // --------------------------
        }

        row.appendChild(actionCell);

        // Deadline
        const deadlineCell = document.createElement('td');
        deadlineCell.className = 'px-4 py-3 text-center';

        if (assignment.has_extended_deadline) {
            // Show both deadlines in a single line with color differentiation
            const deadlineContainer = document.createElement('div');
            deadlineContainer.style.display = 'flex';
            deadlineContainer.style.flexDirection = 'column';
            deadlineContainer.style.gap = '2px';

            if (assignment.extended_deadline) {
                const extendedSpan = document.createElement('div');
                extendedSpan.style.fontSize = '12px';
                extendedSpan.title = 'Extended Deadline';
                extendedSpan.innerHTML = formatDeadline(assignment.extended_deadline);

                if (assignment.submission_file) {
                    extendedSpan.style.color = 'var(--text-secondary)';
                    extendedSpan.style.fontWeight = '500';
                } else {
                    extendedSpan.style.fontWeight = 'bold';
                    if (assignment.is_upcoming) {
                        extendedSpan.style.color = 'var(--warning-color)'; // Extended deadlines should be orange to warn it's a grace period
                    } else if (assignment.is_past_due) {
                        extendedSpan.style.color = 'var(--error-color)';
                    } else {
                        extendedSpan.style.color = 'var(--warning-color)';
                    }
                }
                deadlineContainer.appendChild(extendedSpan);
            }

            if (assignment.actual_deadline) {
                const actualSpan = document.createElement('div');
                actualSpan.style.color = '#6c757d'; // Gray color for original
                actualSpan.style.fontSize = '11px';
                actualSpan.style.textDecoration = 'line-through';
                actualSpan.title = 'Original Deadline (Extended)';
                actualSpan.innerHTML = formatDeadline(assignment.actual_deadline);
                deadlineContainer.appendChild(actualSpan);
            }

            deadlineCell.appendChild(deadlineContainer);
        } else if (assignment.deadline) {
            // Show single deadline with highlighting
            const deadlineSpan = document.createElement('div');
            deadlineSpan.style.fontSize = '12px';
            deadlineSpan.title = 'Deadline';
            deadlineSpan.innerHTML = formatDeadline(assignment.deadline);

            if (assignment.submission_file) {
                deadlineSpan.style.color = 'var(--text-secondary)';
                deadlineSpan.style.fontWeight = '500';
            } else {
                deadlineSpan.style.fontWeight = 'bold';
                if (assignment.is_upcoming) {
                    deadlineSpan.style.color = 'var(--success-color)';
                } else if (assignment.is_past_due) {
                    deadlineSpan.style.color = 'var(--error-color)';
                } else {
                    deadlineSpan.style.color = 'var(--text-secondary)';
                }
            }
            deadlineCell.appendChild(deadlineSpan);
        } else {
            deadlineCell.textContent = '-';
        }

        row.appendChild(deadlineCell);

        return row;
    }

    function escapeHtml(str) {
        if (!str) return '';
        return String(str)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;');
    }

    function showUploadModal(assignment) {
        // Store assignment data for upload function
        window.currentUploadAssignment = assignment;

        const isProduction = (typeof IS_PRODUCTION !== 'undefined' && IS_PRODUCTION === true);
        
        const safeTitle = (assignment && assignment.title)
            ? escapeHtml(assignment.title)
            : 'Untitled Assignment';

        // Context metadata: only clean deadline if present (no course code or assignment number badges)
        let metaPills = '';
        if (assignment && assignment.due_date) {
            const timeStr = assignment.due_time ? ` ${assignment.due_time}` : '';
            metaPills += `<span class="upload-context-pill"><i class="far fa-calendar-check"></i> Due: ${escapeHtml(assignment.due_date)}${escapeHtml(timeStr)}</span>`;
        }

        // Modal Markup
        const modalHtml = `
            <div class="upload-modal-overlay" id="uploadModal" role="dialog" aria-modal="true" aria-labelledby="uploadModalTitle">
                <div class="upload-modal-dialog">
                    
                    <!-- Hairline progress bar (fills during upload) -->
                    <div id="uploadHairlineProgress" class="upload-modal-hairline"></div>

                    <!-- Header -->
                    <div class="upload-modal-header">
                        <h3 id="uploadModalTitle" class="upload-modal-title">Submit Assignment</h3>
                        <button type="button" class="upload-modal-close-btn" id="uploadModalCloseBtn" onclick="closeUploadModal()" aria-label="Close dialog">
                            <i class="fas fa-xmark"></i>
                        </button>
                    </div>

                    <!-- Body -->
                    <div class="upload-modal-body">
                        
                        <!-- Assignment Context -->
                        <div class="upload-context-bar">
                            <div class="upload-context-title">${safeTitle}</div>
                            ${metaPills ? `<div class="upload-context-meta">${metaPills}</div>` : ''}
                        </div>

                        <!-- Inline Error Notice -->
                        <div id="uploadErrorAlert" class="upload-error-alert" role="alert">
                            <i class="fas fa-circle-exclamation"></i>
                            <span id="uploadErrorText" style="flex: 1;"></span>
                        </div>

                        <!-- Solution File Picker -->
                        <div>
                            <input type="file" id="assignmentFile" accept=".pdf,.doc,.docx,.xls,.xlsx,.zip" style="display: none;">

                            <!-- Empty Dropzone -->
                            <div id="dropzoneContainer" class="upload-dropzone" tabindex="0" role="button" aria-label="Click or drop file to upload">
                                <div class="upload-dropzone-icon-circle">
                                    <i class="fas fa-arrow-up-from-bracket"></i>
                                </div>
                                <p class="upload-dropzone-prompt">
                                    Choose file or drag &amp; drop
                                </p>
                                <p class="upload-dropzone-hint">PDF, Word, Excel, or ZIP • Max 15 MB</p>
                            </div>

                            <!-- Selected File Card -->
                            <div id="selectedFileCard" class="upload-file-card" style="display: none;">
                                <div class="upload-file-row">
                                    <div class="upload-file-main">
                                        <div id="fileTypeIconBox" class="upload-file-icon-box">
                                            <i class="fas fa-file-lines"></i>
                                        </div>
                                        <div class="upload-file-details">
                                            <div id="filePreviewName" class="upload-file-name">filename.pdf</div>
                                            <div id="filePreviewSize" class="upload-file-size">0 KB</div>
                                        </div>
                                    </div>
                                    <div class="upload-file-actions">
                                        <button type="button" id="removeFileBtn" class="upload-action-btn" title="Remove file" aria-label="Remove file">
                                            <i class="fas fa-xmark"></i>
                                        </button>
                                    </div>
                                </div>

                                <!-- Live Upload Progress Tracking (shown during upload) -->
                                <div id="uploadProgressSection" class="upload-progress-container" style="display: none;">
                                    <div class="upload-progress-track">
                                        <div id="uploadProgressBarFill" class="upload-progress-fill"></div>
                                    </div>
                                    <div class="upload-progress-meta">
                                        <span id="uploadStatusLabel">
                                            <span class="upload-pulse-dot"></span> Uploading...
                                        </span>
                                        <span id="uploadBytesCounter">0 KB / 0 KB • 0%</span>
                                    </div>
                                </div>
                            </div>
                        </div>

                        ${!isProduction ? `
                        <!-- Dev IDOR Override Section -->
                        <div style="padding-top: 0.875rem; border-top: 1px solid var(--border-color);">
                            <div style="display: flex; align-items: center; justify-content: space-between; margin-bottom: 0.35rem;">
                                <label for="testStudentId" style="margin: 0; font-size: 0.75rem; font-weight: 500; color: var(--text-muted); cursor: pointer;">
                                    Override Student ID
                                </label>
                                <span style="font-size: 0.65rem; background: rgba(245, 158, 11, 0.12); color: var(--warning-color); padding: 1px 5px; border-radius: 4px; font-weight: 600; text-transform: uppercase;">Dev</span>
                            </div>
                            <input type="text" id="testStudentId" class="upload-dev-input" placeholder="Base64 ID (leave blank for default)">
                        </div>
                        ` : ''}
                    </div>

                    <!-- Footer -->
                    <div class="upload-modal-footer">
                        <button type="button" id="cancelUploadBtn" class="upload-modal-btn upload-btn-secondary" onclick="closeUploadModal()">Cancel</button>
                        <button type="button" id="submitAssignmentBtn" class="upload-modal-btn upload-btn-primary" onclick="uploadAssignment(window.currentUploadAssignment)">
                            <i class="fas fa-arrow-up-from-bracket"></i>
                            <span id="submitBtnText">Submit Assignment</span>
                        </button>
                    </div>
                </div>
            </div>
        `;

        // Remove existing modal if any
        const existingModal = document.getElementById('uploadModal');
        if (existingModal) {
            existingModal.remove();
        }

        // Add modal to body
        document.body.insertAdjacentHTML('beforeend', modalHtml);

        // Prevent body scroll
        document.body.style.overflow = 'hidden';

        // Wire up dropzone and interactive controls
        const modal = document.getElementById('uploadModal');
        const fileInput = document.getElementById('assignmentFile');
        const dropzone = document.getElementById('dropzoneContainer');
        const selectedCard = document.getElementById('selectedFileCard');
        const filePreviewName = document.getElementById('filePreviewName');
        const filePreviewSize = document.getElementById('filePreviewSize');
        const fileTypeIconBox = document.getElementById('fileTypeIconBox');
        const removeFileBtn = document.getElementById('removeFileBtn');

        function formatBytes(bytes) {
            if (bytes === 0) return '0 Bytes';
            const k = 1024;
            const sizes = ['Bytes', 'KB', 'MB', 'GB'];
            const i = Math.floor(Math.log(bytes) / Math.log(k));
            return parseFloat((bytes / Math.pow(k, i)).toFixed(1)) + ' ' + sizes[i];
        }

        function getFileTypeTheme(filename) {
            const ext = filename.split('.').pop().toLowerCase();
            if (ext === 'pdf') {
                return { icon: 'fas fa-file-pdf', color: '#ef4444', bg: 'rgba(239, 68, 68, 0.1)' };
            }
            if (['doc', 'docx'].includes(ext)) {
                return { icon: 'fas fa-file-word', color: '#2563eb', bg: 'rgba(37, 99, 235, 0.1)' };
            }
            if (['xls', 'xlsx'].includes(ext)) {
                return { icon: 'fas fa-file-excel', color: '#10b981', bg: 'rgba(16, 185, 129, 0.1)' };
            }
            if (['zip', 'rar'].includes(ext)) {
                return { icon: 'fas fa-file-zipper', color: '#f59e0b', bg: 'rgba(245, 158, 11, 0.1)' };
            }
            return { icon: 'fas fa-file-lines', color: 'var(--primary-color)', bg: 'rgba(37, 99, 235, 0.08)' };
        }

        function setChosenFile(file) {
            if (!file) {
                dropzone.style.display = 'block';
                selectedCard.style.display = 'none';
                return;
            }
            filePreviewName.textContent = file.name;
            filePreviewName.title = file.name;
            filePreviewSize.textContent = formatBytes(file.size);
            
            const theme = getFileTypeTheme(file.name);
            fileTypeIconBox.innerHTML = `<i class="${theme.icon}"></i>`;
            fileTypeIconBox.style.color = theme.color;
            fileTypeIconBox.style.background = theme.bg;

            dropzone.style.display = 'none';
            selectedCard.style.display = 'flex';
        }

        // Dropzone & File actions
        if (dropzone && fileInput) {
            dropzone.addEventListener('click', () => fileInput.click());
            dropzone.addEventListener('keydown', (e) => {
                if (e.key === 'Enter' || e.key === ' ') {
                    e.preventDefault();
                    fileInput.click();
                }
            });

            fileInput.addEventListener('change', function() {
                if (fileInput.files && fileInput.files[0]) {
                    setChosenFile(fileInput.files[0]);
                } else {
                    setChosenFile(null);
                }
            });

            if (removeFileBtn) {
                removeFileBtn.addEventListener('click', (e) => {
                    e.stopPropagation();
                    fileInput.value = '';
                    setChosenFile(null);
                });
            }

            // Drag & Drop handlers for dropzone
            ['dragenter', 'dragover'].forEach(eventName => {
                dropzone.addEventListener(eventName, (e) => {
                    e.preventDefault();
                    e.stopPropagation();
                    dropzone.classList.add('dragover');
                });
            });

            ['dragleave', 'drop'].forEach(eventName => {
                dropzone.addEventListener(eventName, (e) => {
                    e.preventDefault();
                    e.stopPropagation();
                    dropzone.classList.remove('dragover');
                });
            });

            dropzone.addEventListener('drop', (e) => {
                if (e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files.length) {
                    fileInput.files = e.dataTransfer.files;
                    setChosenFile(e.dataTransfer.files[0]);
                }
            });

            // Also support drag-and-drop replacement directly onto the selected card
            ['dragenter', 'dragover'].forEach(eventName => {
                selectedCard.addEventListener(eventName, (e) => {
                    e.preventDefault();
                    e.stopPropagation();
                    selectedCard.style.borderColor = 'var(--primary-color)';
                });
            });

            ['dragleave', 'drop'].forEach(eventName => {
                selectedCard.addEventListener(eventName, (e) => {
                    e.preventDefault();
                    e.stopPropagation();
                    selectedCard.style.borderColor = '';
                });
            });

            selectedCard.addEventListener('drop', (e) => {
                if (e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files.length) {
                    fileInput.files = e.dataTransfer.files;
                    setChosenFile(e.dataTransfer.files[0]);
                }
            });
        }

        // Close on backdrop click (if not uploading)
        if (modal) {
            modal.addEventListener('click', function(e) {
                if (e.target === modal && modal.dataset.uploading !== 'true') {
                    closeUploadModal();
                }
            });
        }

        // Close on Escape key (if not uploading)
        const onModalKeyDown = function(e) {
            if (e.key === 'Escape') {
                const activeModal = document.getElementById('uploadModal');
                if (activeModal && activeModal.dataset.uploading !== 'true') {
                    closeUploadModal();
                    document.removeEventListener('keydown', onModalKeyDown);
                }
            }
        };
        document.addEventListener('keydown', onModalKeyDown);
    }

    // Make functions globally accessible
    window.closeUploadModal = function () {
        const modal = document.getElementById('uploadModal');
        if (!modal || modal.dataset.uploading === 'true') return;
        
        const dialog = modal.querySelector('.upload-modal-dialog');
        modal.style.animation = 'modalOverlayFadeOut 0.15s cubic-bezier(0.16, 1, 0.3, 1) forwards';
        if (dialog) {
            dialog.style.animation = 'modalDialogSlideDown 0.15s cubic-bezier(0.16, 1, 0.3, 1) forwards';
        }
        setTimeout(() => {
            modal.remove();
            // Restore body scroll
            document.body.style.overflow = '';
        }, 140);
    };

    function showDeleteModal(assignment) {
        // Store assignment data for delete function
        window.currentDeleteAssignment = assignment;

        // Create confirmation modal with improved styling
        const modalHtml = `
            <div class="modal-overlay" id="deleteModal" style="position: fixed; top: 0; left: 0; right: 0; bottom: 0; background: rgba(0,0,0,0.6); display: flex; align-items: center; justify-content: center; z-index: 9999; animation: fadeIn 0.2s ease-out;">
                <div class="modal-container" style="background: var(--bg-primary); border-radius: var(--border-radius-lg); box-shadow: 0 20px 60px rgba(0,0,0,0.3); max-width: 500px; width: 90%; overflow: hidden; animation: slideUp 0.3s ease-out;">
                    <div class="modal-header" style="background: linear-gradient(135deg, var(--error-color), #dc2626); color: white; padding: 1.5rem; display: flex; justify-content: space-between; align-items: center;">
                        <h3 style="margin: 0; font-weight: 600; font-size: 1.25rem;"><i class="fa fa-trash" style="margin-right: 0.5rem;"></i>Delete Submission</h3>
                        <button type="button" onclick="closeDeleteModal()" style="background: rgba(255,255,255,0.2); border: 0; color: white; font-size: 1.5rem; cursor: pointer; padding: 0.25rem 0.5rem; border-radius: 4px; transition: var(--transition); line-height: 1;" onmouseover="this.style.background='rgba(255,255,255,0.3)'" onmouseout="this.style.background='rgba(255,255,255,0.2)'">&times;</button>
                    </div>
                    <div class="modal-body" style="padding: 2rem 1.5rem;">
                        <div style="text-align: center;">
                            <div style="width: 64px; height: 64px; margin: 0 auto 1.5rem; background: linear-gradient(135deg, #fee2e2, #fecaca); border-radius: 50%; display: flex; align-items: center; justify-content: center;">
                                <i class="fa fa-exclamation-triangle" style="font-size: 2rem; color: var(--error-color);"></i>
                            </div>
                            <h4 style="margin: 0 0 0.75rem 0; color: var(--text-primary); font-weight: 600; font-size: 1.125rem;">Confirm Deletion</h4>
                            <p style="margin: 0 0 0.5rem 0; color: var(--text-secondary); font-size: 0.9375rem;">Are you sure you want to delete the submission for:</p>
                            <p style="margin: 0; color: var(--text-primary); font-weight: 600; font-size: 1rem; padding: 0.75rem; background: var(--bg-secondary); border-radius: var(--border-radius); border-left: 4px solid var(--error-color);">${escapeHtml(assignment.title)}</p>
                            <p style="margin: 1rem 0 0 0; color: var(--text-muted); font-size: 0.8125rem;"><i class="fa fa-info-circle"></i> This action cannot be undone.</p>
                        </div>
                    </div>
                    <div class="modal-footer" style="border-top: 1px solid var(--border-color); padding: 1.25rem 1.5rem; display: flex; justify-content: flex-end; gap: 0.75rem; background: var(--bg-secondary);">
                        <button type="button" onclick="closeDeleteModal()" style="padding: 0.625rem 1.25rem; border-radius: var(--border-radius); border: 1px solid var(--border-color); background: var(--bg-primary); color: var(--text-primary); cursor: pointer; font-weight: 500; transition: var(--transition); font-size: 0.875rem;" onmouseover="this.style.background='var(--bg-tertiary)'" onmouseout="this.style.background='var(--bg-primary)'">Cancel</button>
                        <button type="button" onclick="deleteAssignment(window.currentDeleteAssignment)" style="padding: 0.625rem 1.5rem; border-radius: var(--border-radius); border: none; background: linear-gradient(135deg, var(--error-color), #dc2626); color: white; cursor: pointer; font-weight: 600; transition: var(--transition); box-shadow: 0 2px 8px rgba(239, 68, 68, 0.3); font-size: 0.875rem;" onmouseover="this.style.transform='translateY(-1px)'; this.style.boxShadow='0 4px 12px rgba(239, 68, 68, 0.4)'" onmouseout="this.style.transform='translateY(0)'; this.style.boxShadow='0 2px 8px rgba(239, 68, 68, 0.3)'">
                            <i class="fa fa-trash" style="margin-right: 0.5rem;"></i>Delete Submission
                        </button>
                    </div>
                </div>
            </div>
            <style>
                @keyframes fadeIn {
                    from { opacity: 0; }
                    to { opacity: 1; }
                }
                @keyframes fadeOut {
                    from { opacity: 1; }
                    to { opacity: 0; }
                }
                @keyframes slideUp {
                    from { transform: translateY(20px); opacity: 0; }
                    to { transform: translateY(0); opacity: 1; }
                }
            </style>
        `;

        // Remove existing modal if any
        const existingModal = document.getElementById('deleteModal');
        if (existingModal) {
            existingModal.remove();
        }

        // Add modal to body
        document.body.insertAdjacentHTML('beforeend', modalHtml);

        // Prevent body scroll
        document.body.style.overflow = 'hidden';
    }

    window.closeDeleteModal = function () {
        const modal = document.getElementById('deleteModal');
        if (modal) {
            modal.style.animation = 'fadeOut 0.2s ease-out';
            setTimeout(() => {
                modal.remove();
                // Restore body scroll
                document.body.style.overflow = '';
            }, 200);
        }
    };

    window.uploadAssignment = function (assignmentData) {
        console.log('Upload button clicked', assignmentData);
        const modal = document.getElementById('uploadModal');
        const fileInput = document.getElementById('assignmentFile');
        const dropzone = document.getElementById('dropzoneContainer');
        const selectedCard = document.getElementById('selectedFileCard');
        const errorAlert = document.getElementById('uploadErrorAlert');
        const errorText = document.getElementById('uploadErrorText');
        const hairline = document.getElementById('uploadHairlineProgress');
        const progressSection = document.getElementById('uploadProgressSection');
        const progressBarFill = document.getElementById('uploadProgressBarFill');
        const statusLabel = document.getElementById('uploadStatusLabel');
        const bytesCounter = document.getElementById('uploadBytesCounter');
        const submitBtn = document.getElementById('submitAssignmentBtn');
        const cancelBtn = document.getElementById('cancelUploadBtn');
        const closeBtn = document.getElementById('uploadModalCloseBtn');
        const removeBtn = document.getElementById('removeFileBtn');

        if (!fileInput) {
            console.error('File input not found');
            alert('Error: File input not found. Please try again.');
            return;
        }

        function triggerError(msg) {
            if (errorAlert && errorText) {
                errorText.textContent = msg;
                errorAlert.style.display = 'flex';
            } else {
                alert(msg);
            }
            const shakeTarget = (selectedCard && selectedCard.style.display !== 'none') ? selectedCard : dropzone;
            if (shakeTarget) {
                shakeTarget.classList.remove('upload-shake');
                void shakeTarget.offsetWidth;
                shakeTarget.classList.add('upload-shake');
                setTimeout(() => shakeTarget.classList.remove('upload-shake'), 320);
            }
        }

        if (!fileInput.files || !fileInput.files[0]) {
            triggerError('Please choose or drop a solution file to upload.');
            return;
        }

        const file = fileInput.files[0];
        const maxSize = ASSIGNMENT_UPLOAD_MAX_SIZE;

        // Validate file size
        if (file.size > maxSize) {
            triggerError('File size exceeds the 15MB limit.');
            return;
        }

        // Validate file extension
        const allowedExtensions = ['pdf', 'doc', 'docx', 'xls', 'xlsx', 'zip'];
        const fileName = file.name.toLowerCase();
        const fileExtension = fileName.split('.').pop();

        if (!allowedExtensions.includes(fileExtension)) {
            triggerError('Invalid file type. Only PDF, DOC, DOCX, XLS, XLSX & ZIP formats are supported.');
            return;
        }

        // Check for special characters in filename
        const specialChars = /[#$%^&*()+=\[\]{};':"\\|<>?]/;
        if (specialChars.test(file.name)) {
            triggerError('Filename contains special symbols (#, $, etc). Please rename the file and try again.');
            return;
        }

        // Allow IDOR test override (dev/non-production only)
        const isProduction = (typeof IS_PRODUCTION !== 'undefined' && IS_PRODUCTION === true);
        const testStudentIdEl = document.getElementById('testStudentId');
        const testStudentIdVal = (!isProduction && testStudentIdEl) ? testStudentIdEl.value.trim() : '';

        if (errorAlert) errorAlert.style.display = 'none';

        // Lock modal into active uploading state
        if (modal) modal.dataset.uploading = 'true';
        if (closeBtn) closeBtn.style.display = 'none';
        if (cancelBtn) {
            cancelBtn.disabled = true;
            cancelBtn.style.display = 'none';
        }
        if (removeBtn) removeBtn.style.display = 'none';

        if (hairline) {
            hairline.style.opacity = '1';
            hairline.style.width = '12%';
            hairline.style.backgroundColor = 'var(--primary-color)';
        }
        if (progressSection) {
            progressSection.style.display = 'flex';
        }
        if (progressBarFill) {
            progressBarFill.style.width = '12%';
            progressBarFill.classList.remove('is-success');
        }
        if (statusLabel) {
            statusLabel.innerHTML = '<span class="upload-pulse-dot"></span> Preparing upload...';
        }
        if (bytesCounter) {
            bytesCounter.textContent = 'Connecting...';
        }
        if (submitBtn) {
            submitBtn.disabled = true;
            submitBtn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> <span>Uploading...</span>';
        }

        function restoreFormOnError(msg) {
            if (modal) modal.dataset.uploading = 'false';
            if (closeBtn) closeBtn.style.display = '';
            if (cancelBtn) {
                cancelBtn.disabled = false;
                cancelBtn.style.display = '';
            }
            if (removeBtn) removeBtn.style.display = '';

            if (hairline) {
                hairline.style.opacity = '0';
                hairline.style.width = '0%';
            }
            if (progressSection) {
                progressSection.style.display = 'none';
            }
            if (progressBarFill) {
                progressBarFill.style.width = '0%';
                progressBarFill.classList.remove('is-success');
            }
            if (submitBtn) {
                submitBtn.disabled = false;
                submitBtn.classList.remove('is-success');
                submitBtn.innerHTML = '<i class="fas fa-arrow-up-from-bracket"></i> <span>Submit Assignment</span>';
            }
            triggerError(msg);
        }

        // 1. Get secure Edge Upload Token from Cloud Run
        fetch('/lms/api/assignments/upload-token', { method: 'POST' })
            .then(res => res.json())
            .then(tokenData => {
                if (!tokenData.success) {
                    restoreFormOnError(tokenData.error || 'Could not prepare upload session.');
                    return;
                }

                if (hairline) hairline.style.width = '20%';
                if (progressBarFill) progressBarFill.style.width = '20%';
                if (statusLabel) {
                    statusLabel.innerHTML = '<span class="upload-pulse-dot"></span> Uploading solution file...';
                }

                // 2. Prepare LMS-Compliant FormData
                const formData = new FormData();
                formData.append('assignmentSubmissionFile', file);
                
                if (assignmentData && assignmentData.assignment_id) {
                    formData.append('assignmentID', assignmentData.assignment_id);
                }
                
                const offeredCourseId = (assignmentData && assignmentData.offered_course_id) || (currentCourse ? btoa(currentCourse) : null);
                if (offeredCourseId) {
                    formData.append('offeredCourseID', offeredCourseId);
                    formData.append('ocID', offeredCourseId);
                }
                
                // Allow IDOR test override (dev/non-production only)
                if (testStudentIdVal !== '') {
                    formData.append('studentID', testStudentIdVal);
                } else if (assignmentData && assignmentData.student_id) {
                    formData.append('studentID', assignmentData.student_id);
                }

                // 3. Stream upload using XHR for live progress tracking
                const xhr = new XMLHttpRequest();
                xhr.open('POST', tokenData.upload_url, true);

                xhr.upload.onprogress = function(e) {
                    if (e.lengthComputable) {
                        const percent = Math.min(95, Math.max(20, Math.round((e.loaded / e.total) * 100)));
                        if (hairline) hairline.style.width = percent + '%';
                        if (progressBarFill) progressBarFill.style.width = percent + '%';
                        if (bytesCounter) {
                            const fmt = (b) => b < 1048576 ? (b / 1024).toFixed(1) + ' KB' : (b / 1048576).toFixed(2) + ' MB';
                            bytesCounter.textContent = `${fmt(e.loaded)} / ${fmt(e.total)} • ${percent}%`;
                        }
                        if (statusLabel) {
                            statusLabel.innerHTML = '<span class="upload-pulse-dot"></span> Uploading...';
                        }
                        if (submitBtn) {
                            submitBtn.innerHTML = `<i class="fas fa-spinner fa-spin"></i> <span>Uploading ${percent}%...</span>`;
                        }
                    }
                };

                xhr.onload = function() {
                    if (hairline) hairline.style.width = '98%';
                    if (progressBarFill) progressBarFill.style.width = '98%';
                    if (statusLabel) {
                        statusLabel.innerHTML = '<span class="upload-pulse-dot" style="background: var(--warning-color);"></span> Submitting to LMS portal...';
                    }

                    if (xhr.status >= 200 && xhr.status < 400) {
                        let isSuccess = false;
                        let errorMsg = 'LMS rejected upload.';
                        try {
                            const data = JSON.parse(xhr.responseText);
                            const rawError = (data.error || '').toString();
                            const rawMsg = (data.message || '').toString();

                            // LMS returns redirect with &r=successAddAssignmentSubmission on success
                            if (data.success === true ||
                                rawError.toLowerCase().includes('success') ||
                                rawMsg.toLowerCase().includes('success')) {
                                isSuccess = true;
                            } else {
                                errorMsg = rawError || rawMsg || errorMsg;
                            }
                        } catch (e) {
                            if (xhr.responseText && xhr.responseText.toLowerCase().includes('success')) {
                                isSuccess = true;
                            } else if (xhr.responseText && (xhr.responseText.toLowerCase().includes('error') || xhr.responseText.toLowerCase().includes('failed'))) {
                                errorMsg = 'LMS returned an error page.';
                            } else {
                                isSuccess = true;
                            }
                        }

                        if (isSuccess) {
                            if (hairline) {
                                hairline.style.width = '100%';
                                hairline.style.backgroundColor = 'var(--success-color)';
                            }
                            if (progressBarFill) {
                                progressBarFill.style.width = '100%';
                                progressBarFill.classList.add('is-success');
                            }
                            if (statusLabel) {
                                statusLabel.innerHTML = '<i class="fas fa-circle-check text-success" style="margin-right: 4px;"></i> Submission successful!';
                            }
                            if (bytesCounter) {
                                bytesCounter.textContent = 'Completed';
                            }
                            if (submitBtn) {
                                submitBtn.classList.add('is-success');
                                submitBtn.innerHTML = '<i class="fas fa-check"></i> <span>Submitted</span>';
                            }
                            
                            // Tactile completion delay (350ms)
                            setTimeout(() => {
                                if (modal) modal.dataset.uploading = 'false';
                                closeUploadModal();
                                showNotificationBanner('success', 'Assignment Submitted', `${file.name} was successfully submitted.`);
                                
                                const targetCourse = currentCourse || (assignmentData && assignmentData.course_code) || (courseSelect ? courseSelect.value : null);
                                const targetSemester = currentSemester || (semesterSelect ? semesterSelect.value : null);
                                if (targetCourse) {
                                    setTimeout(() => loadAssignments(targetSemester, targetCourse, true), 100);
                                }
                            }, 350);
                        } else {
                            restoreFormOnError(errorMsg);
                        }
                    } else {
                        restoreFormOnError(`Server returned status ${xhr.status}. Please try again.`);
                    }
                };

                xhr.onerror = function() {
                    restoreFormOnError('A network error occurred during upload.');
                };

                xhr.send(formData);
            })
            .catch(error => {
                console.error('Token generation error:', error);
                restoreFormOnError('Failed to reach upload preparation server.');
            });
    };

    // --- GROUP UPLOAD MODAL ---
    window.showGroupUploadModal = function (assignment) {
        if (typeof GROUPS_ENABLED === 'undefined' || !GROUPS_ENABLED) return;
        window.currentUploadAssignment = assignment;

        const modalHtml = `
            <div class="modal-overlay" id="uploadModal" style="position: fixed; top: 0; left: 0; right: 0; bottom: 0; background: rgba(0,0,0,0.6); display: flex; align-items: center; justify-content: center; z-index: 9999; animation: fadeIn 0.2s ease-out;">
                <div class="modal-container" style="background: var(--bg-primary); border-radius: var(--border-radius-lg); box-shadow: 0 20px 60px rgba(0,0,0,0.3); max-width: 600px; width: 90%; max-height: 90vh; overflow: hidden; animation: slideUp 0.3s ease-out; display: flex; flex-direction: column;">
                    <div class="modal-header" style="background: linear-gradient(135deg, #17a2b8, #117a8b); color: white; padding: 1.5rem; display: flex; justify-content: space-between; align-items: center;">
                        <h3 style="margin: 0; font-weight: 600; font-size: 1.25rem;"><i class="fa fa-users" style="margin-right: 0.5rem;"></i>Group Assignment Upload</h3>
                        <button type="button" onclick="closeUploadModal()" style="background: rgba(255,255,255,0.2); border: 0; color: white; font-size: 1.5rem; cursor: pointer; padding: 0.25rem 0.5rem; border-radius: 4px; transition: var(--transition); line-height: 1;" onmouseover="this.style.background='rgba(255,255,255,0.3)'" onmouseout="this.style.background='rgba(255,255,255,0.2)'">&times;</button>
                    </div>
                    
                    <!-- Tab Headers -->
                    <div style="display: flex; background: var(--bg-secondary); border-bottom: 1px solid var(--border-color);">
                        <button id="tabBtn-submission" onclick="switchGroupTab('submission')" style="flex: 1; padding: 1rem; background: var(--bg-primary); border: none; border-bottom: 2px solid #17a2b8; font-weight: 600; cursor: pointer; color: #17a2b8;">Submission</button>
                        <button id="tabBtn-audit" onclick="switchGroupTab('audit')" style="flex: 1; padding: 1rem; background: transparent; border: none; border-bottom: 2px solid transparent; font-weight: 500; cursor: pointer; color: var(--text-secondary);">Audit Log</button>
                    </div>

                    <div class="modal-body" style="padding: 1.5rem; flex: 1; overflow-y: auto;">
                        
                        <!-- SUBMISSION TAB -->
                        <div id="tab-submission" style="display: block;">
                            <div style="margin-bottom: 1.25rem;">
                                <label style="display: block; margin-bottom: 0.5rem; font-weight: 600; color: var(--text-primary); font-size: 0.875rem;">Assignment Title</label>
                                <input type="text" value="${escapeHtml(assignment.title)}" disabled style="display: block; width: 100%; padding: 0.75rem; border: 1px solid var(--border-color); border-radius: var(--border-radius); background: var(--bg-tertiary); color: var(--text-secondary); font-size: 0.875rem;">
                            </div>
                            <div style="margin-bottom: 1.25rem;">
                                <label for="groupEnrollments" style="display: block; font-weight: 600; margin-bottom: 0.5rem; color: var(--text-primary); font-size: 0.875rem;">
                                    Group Members (Enrollment Numbers)
                                </label>
                                <textarea id="groupEnrollments" placeholder="e.g. 02-000000-009, 02-000000-015" rows="2" style="width: 100%; padding: 0.75rem; border: 1px solid var(--border-color); border-radius: var(--border-radius); font-size: 0.9rem; background: var(--bg-secondary); color: var(--text-primary); transition: var(--transition); resize: vertical;"></textarea>
                                <small style="display: block; margin-top: 0.25rem; color: var(--text-muted);">Comma separated. These members will receive invites. Once accepted, their accounts will upload this file simultaneously.</small>
                            </div>
                            ${!assignment.submission_file ? `
                            <div style="margin-bottom: 0.5rem;">
                                <label for="assignmentFile" style="display: block; margin-bottom: 0.5rem; font-weight: 600; color: var(--text-primary); font-size: 0.875rem;">Solution File (Max 15MB) *</label>
                                <input type="file" id="assignmentFile" required accept=".pdf,.doc,.docx,.xls,.xlsx,.zip" style="display: block; width: 100%; padding: 0.75rem; border: 2px dashed var(--border-color); border-radius: var(--border-radius); background: var(--bg-secondary); color: var(--text-primary); font-size: 0.875rem; cursor: pointer; transition: var(--transition);" onchange="this.style.borderColor='var(--primary-color)'" onfocus="this.style.borderColor='var(--primary-color)'" onblur="this.style.borderColor='var(--border-color)'">
                                <small style="display: block; margin-top: 0.25rem; color: var(--text-muted);">Allowed formats: pdf, doc, docx, xls, xlsx, zip. Commas are allowed in filenames.</small>
                            </div>
                            ` : `
                            <div style="margin-bottom: 0.5rem; padding: 1rem; background: #fff3cd; border-left: 4px solid #ffc107; border-radius: 4px; color: #856404;">
                                <strong><i class="fa fa-info-circle"></i> Submission Exists</strong><br/>
                                You have already submitted a file for this assignment. If you wish to upload a new one, you must delete the current group submission first.
                            </div>
                            `}
                        </div>

                        <!-- AUDIT TAB -->
                        <div id="tab-audit" style="display: none;">
                            <div class="table-responsive">
                                <table class="table table-sm">
                                    <thead><tr><th>Time</th><th>Actor</th><th>Action</th><th>Status</th></tr></thead>
                                    <tbody id="groupAuditTableBody">
                                        <tr><td colspan="4" class="text-center">Submit this group first to view logs.</td></tr>
                                    </tbody>
                                </table>
                            </div>
                        </div>

                    </div>
                    <div class="modal-footer" id="modal-footer-submission" style="border-top: 1px solid var(--border-color); padding: 1.25rem 1.5rem; display: flex; justify-content: flex-end; gap: 0.75rem; background: var(--bg-secondary);">
                        <button type="button" onclick="closeUploadModal()" style="padding: 0.625rem 1.25rem; border-radius: var(--border-radius); border: 1px solid var(--border-color); background: var(--bg-primary); color: var(--text-primary); cursor: pointer; font-weight: 500; transition: var(--transition); font-size: 0.875rem;" onmouseover="this.style.background='var(--bg-tertiary)'" onmouseout="this.style.background='var(--bg-primary)'">Cancel</button>
                        ${!assignment.submission_file ? `
                        <button type="button" onclick="uploadGroupAssignment(window.currentUploadAssignment)" style="padding: 0.625rem 1.5rem; border-radius: var(--border-radius); border: none; background: linear-gradient(135deg, #17a2b8, #117a8b); color: white; cursor: pointer; font-weight: 600; transition: var(--transition); box-shadow: 0 2px 8px rgba(23, 162, 184, 0.3); font-size: 0.875rem;">
                            <i class="fa fa-upload" style="margin-right: 0.5rem;"></i>Create & Upload
                        </button>
                        ` : `
                        <button type="button" onclick="deleteGroupAssignment(window.currentUploadAssignment)" style="padding: 0.625rem 1.5rem; border-radius: var(--border-radius); border: none; background: linear-gradient(135deg, #dc3545, #c82333); color: white; cursor: pointer; font-weight: 600; transition: var(--transition); box-shadow: 0 2px 8px rgba(220, 53, 69, 0.3); font-size: 0.875rem;">
                            <i class="fas fa-users-slash" style="margin-right: 0.5rem;"></i>Delete Group Submission
                        </button>
                        `}
                    </div>
                </div>
            </div>
            <style>
                @keyframes fadeIn { from { opacity: 0; } to { opacity: 1; } }
                @keyframes slideUp { from { transform: translateY(20px); opacity: 0; } to { transform: translateY(0); opacity: 1; } }
            </style>
        `;

        const existingModal = document.getElementById('uploadModal');
        if (existingModal) existingModal.remove();

        document.body.insertAdjacentHTML('beforeend', modalHtml);
        document.body.style.overflow = 'hidden';
        
        // Fetch logs and existing partners
        let assignmentId = assignment.assignment_id;
        if (!assignmentId && assignment.assignment_file) {
            try {
                const urlStr = assignment.assignment_file;
                if (urlStr.includes('k=')) {
                    const urlParams = new URLSearchParams(urlStr.substring(urlStr.indexOf('?')));
                    const kParam = urlParams.get('k');
                    if (kParam) {
                        const numericId = atob(kParam).split(',')[0];
                        if (numericId && !isNaN(numericId)) assignmentId = btoa(numericId);
                    }
                }
            } catch(e) {}
        }
        
        if (assignmentId) {
            fetch(`/lms/api/groups/assignment_logs?assignment_id=${assignmentId}&course_id=${currentCourse}`)
            .then(res => res.json())
            .then(data => {
                if (data.success) {
                    // Populate partners
                    if (data.partners && data.partners.length > 0) {
                        const ta = document.getElementById('groupEnrollments');
                        if (ta) ta.value = data.partners.join(', ');
                    }
                    
                    // Populate logs
                    const tbody = document.getElementById('groupAuditTableBody');
                    if (tbody && data.logs && data.logs.length > 0) {
                        tbody.innerHTML = '';
                        // Reverse logs to show newest first
                        const sortedLogs = [...data.logs].reverse();
                        sortedLogs.forEach(log => {
                            const tr = document.createElement('tr');
                            
                            // Parse timestamp elegantly
                            const dt = new Date(log.timestamp);
                            const timeStr = dt.toLocaleTimeString([], {hour: '2-digit', minute:'2-digit'});
                            const dateStr = dt.toLocaleDateString();
                            
                            let statusBadge = '';
                            if (log.action.includes('Success')) statusBadge = '<span class="badge bg-success">Success</span>';
                            else if (log.action.includes('Failed') || log.action.includes('Error')) statusBadge = '<span class="badge bg-danger">Failed</span>';
                            else statusBadge = '<span class="badge bg-info">Info</span>';
                            
                            tr.innerHTML = `
                                <td><small>${dateStr}<br/>${timeStr}</small></td>
                                <td>${escapeHtml(log.actor)}</td>
                                <td>${escapeHtml(log.action)}<br/><small class="text-muted">${escapeHtml(log.sync_status)}</small></td>
                                <td>${statusBadge}</td>
                            `;
                            tbody.appendChild(tr);
                        });
                    }
                    
                    // Lock input if uploading or deleting
                    if (data.state && data.state !== 'idle') {
                        const fileInput = document.getElementById('assignmentFile');
                        const ta = document.getElementById('groupEnrollments');
                        const btn = document.querySelector('#modal-footer-submission button:nth-child(2)');
                        
                        if (fileInput) fileInput.disabled = true;
                        if (ta) ta.disabled = true;
                        if (btn) {
                            btn.disabled = true;
                            btn.innerHTML = `<i class="fa fa-spinner fa-spin"></i> ${data.state === 'uploading' ? 'Uploading...' : 'Processing...'}`;
                            btn.style.opacity = '0.7';
                        }
                    }
                }
            })
            .catch(e => console.error("Error fetching group logs:", e));
        }
    };

    window.switchGroupTab = function(tabName) {
        if(tabName === 'submission') {
            document.getElementById('tab-submission').style.display = 'block';
            document.getElementById('tab-audit').style.display = 'none';
            document.getElementById('tabBtn-submission').style.cssText = 'flex: 1; padding: 1rem; background: var(--bg-primary); border: none; border-bottom: 2px solid #17a2b8; font-weight: 600; cursor: pointer; color: #17a2b8;';
            document.getElementById('tabBtn-audit').style.cssText = 'flex: 1; padding: 1rem; background: transparent; border: none; border-bottom: 2px solid transparent; font-weight: 500; cursor: pointer; color: var(--text-secondary);';
            document.getElementById('modal-footer-submission').style.display = 'flex';
        } else {
            document.getElementById('tab-submission').style.display = 'none';
            document.getElementById('tab-audit').style.display = 'block';
            document.getElementById('tabBtn-submission').style.cssText = 'flex: 1; padding: 1rem; background: transparent; border: none; border-bottom: 2px solid transparent; font-weight: 500; cursor: pointer; color: var(--text-secondary);';
            document.getElementById('tabBtn-audit').style.cssText = 'flex: 1; padding: 1rem; background: var(--bg-primary); border: none; border-bottom: 2px solid #17a2b8; font-weight: 600; cursor: pointer; color: #17a2b8;';
            document.getElementById('modal-footer-submission').style.display = 'none';
        }
    };

    window.uploadGroupAssignment = function (assignmentData) {
        console.log('Group Upload button clicked', assignmentData);
        const fileInput = document.getElementById('assignmentFile');

        if (!fileInput || !fileInput.files[0]) {
            alert('Please select a file to upload.');
            return;
        }
        
        const file = fileInput.files[0];
        
        // Validation: 15MB limit
        if (file.size > ASSIGNMENT_UPLOAD_MAX_SIZE) {
            alert('File is too large. Maximum size is 15MB.');
            return;
        }
        
        // Validation: Allowed Extensions
        const allowedExts = ['pdf', 'doc', 'docx', 'xls', 'xlsx', 'zip'];
        const ext = file.name.split('.').pop().toLowerCase();
        if (!allowedExts.includes(ext)) {
            alert('Invalid file type. Allowed formats: pdf, doc, docx, xls, xlsx, zip.');
            return;
        }
        
        // Validation: No special symbols
        if (/[#$%^&*()+=\[\]{};':"\\|<>?]/.test(file.name)) {
            alert('File name contains special symbols. Please rename the file and try again.');
            return;
        }

        let assignmentId = assignmentData.assignment_id;

        // Fallback: Extract from assignment_file URL if missing
        if (!assignmentId && assignmentData.assignment_file) {
            try {
                const urlStr = assignmentData.assignment_file;
                if (urlStr.includes('k=')) {
                    const urlParams = new URLSearchParams(urlStr.substring(urlStr.indexOf('?')));
                    const kParam = urlParams.get('k');
                    if (kParam) {
                        const decodedK = atob(kParam);
                        const numericId = decodedK.split(',')[0];
                        if (numericId && !isNaN(numericId)) {
                            assignmentId = btoa(numericId);
                        }
                    }
                }
            } catch (e) {
                console.error('Error auto-detecting assignment ID:', e);
            }
        }

        if (!assignmentId) {
            alert('Assignment ID could not be auto-detected. This course might be missing data.');
            return;
        }

        const groupEnrollmentsInput = document.getElementById('groupEnrollments');
        const partners = groupEnrollmentsInput ? groupEnrollmentsInput.value.split(',').map(s => s.trim()).filter(s => s) : [];

        closeUploadModal();
        showLoadingOverlay('Creating group and uploading...');

        // 1. Create Group first
        fetch('/lms/api/groups/create', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                assignment_id: assignmentId,
                course_id: currentCourse,
                partners: partners
            })
        })
        .then(res => res.json())
        .then(data => {
            if(!data.success) throw new Error(data.error || 'Failed to create group');
            
            // 2. Actually upload the file via /lms/api/groups/upload (To be implemented)
            // For now, we will use the standard upload with a group_id flag
            const formData = new FormData();
            formData.append('file', file);
            formData.append('assignment_no', assignmentData.assignment_no);
            formData.append('semester', currentSemester);
            formData.append('course', currentCourse);
            formData.append('assignment_id', assignmentId);
            formData.append('group_id', data.group_id);
            
            if (assignmentData.offered_course_id) {
                formData.append('offered_course_id', assignmentData.offered_course_id);
            } else if (currentCourse) {
                formData.append('offered_course_id', btoa(currentCourse));
            }
            
            if (assignmentData.student_id) formData.append('student_id', assignmentData.student_id);
            
            return fetch('/lms/api/groups/upload', {
                method: 'POST',
                body: formData
            });
        })
        .then(response => response.json())
        .then(data => {
            hideLoadingOverlay();
            if (data.success) {
                showNotificationBanner('success', 'Group Upload Started', 'Your assignment is being uploaded for accepted members.');
                setTimeout(() => loadAssignments(currentSemester, currentCourse, true), 1000);
            } else {
                showNotificationBanner('error', 'Group Upload Failed', data.error || 'Server rejected the upload');
            }
        })
        .catch(error => {
            console.error('Group Upload error:', error);
            hideLoadingOverlay();
            showNotificationBanner('error', 'Upload Error', error.message || 'Exception occurred during group upload.');
        });
    };
    // --- GROUP UPLOAD END ---
    
    // --- GROUP DELETE START ---
    window.deleteGroupAssignment = function(assignmentData) {
        console.log('Group Delete button clicked', assignmentData);
        
        let assignmentId = assignmentData.assignment_id;

        // Fallback: Extract from assignment_file URL if missing
        if (!assignmentId && assignmentData.assignment_file) {
            try {
                const urlStr = assignmentData.assignment_file;
                if (urlStr.includes('k=')) {
                    const urlParams = new URLSearchParams(urlStr.substring(urlStr.indexOf('?')));
                    const kParam = urlParams.get('k');
                    if (kParam) {
                        const numericId = atob(kParam).split(',')[0];
                        if (numericId && !isNaN(numericId)) assignmentId = btoa(numericId);
                    }
                }
            } catch (e) {}
        }

        if (!assignmentId) {
            alert('Assignment ID could not be auto-detected. This course might be missing data.');
            return;
        }

        // We first need the group ID to issue a delete. The backend can lookup the group via assignment_id.
        closeUploadModal();
        showLoadingOverlay('Looking up group details...');

        // First, fetch the group logs to get the group_id
        fetch(`/lms/api/groups/assignment_logs?assignment_id=${assignmentId}&course_id=${currentCourse}`)
        .then(res => res.json())
        .then(data => {
            if (!data.success || !data.logs || data.logs.length === 0) {
                // Not a group submission or group not found. Fallback to normal delete.
                hideLoadingOverlay();
                const confirmFallback = confirm("No group found for this assignment. Delete just your own submission?");
                if (confirmFallback) {
                    window.deleteAssignment(assignmentData);
                }
                return;
            }
            
            // We have a group! But the API response doesn't actually give us the group_id explicitly right now.
            // Wait, we need the group_id to call /api/groups/delete.
            // Let's modify the delete endpoint to accept assignment_id and course_id, and look up the group itself.
            
            // Or we can just send the assignmentData to /api/groups/delete
            const deleteData = {
                group_id: data.group_id,
                assignment_no: assignmentData.assignment_no,
                semester: currentSemester,
                course: currentCourse,
                assignment_id: assignmentId,
                submission_id: assignmentData.submission_id,
                offered_course_id: assignmentData.offered_course_id || btoa(currentCourse)
            };
            
            showLoadingOverlay('Deleting group submission...');
            
            return fetch('/lms/api/groups/delete', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(deleteData)
            });
        })
        .then(res => {
            if (!res) return; // Promise chained aborted
            return res.json();
        })
        .then(data => {
            if (!data) return;
            hideLoadingOverlay();
            if (data.success) {
                showNotificationBanner('success', 'Group Delete Started', 'The assignment is being deleted for all accepted members.');
                setTimeout(() => loadAssignments(currentSemester, currentCourse, true), 1000);
            } else {
                showNotificationBanner('error', 'Group Delete Failed', data.error || 'Server rejected the delete request');
            }
        })
        .catch(error => {
            console.error('Group Delete error:', error);
            hideLoadingOverlay();
            showNotificationBanner('error', 'Delete Error', error.message || 'Exception occurred during group delete.');
        });
    };
    // --- GROUP DELETE END ---
    
    window.deleteAssignment = function(assignmentData) {
        console.log('Delete button clicked', assignmentData);
        
        // Prepare delete data with LMS IDs
        const deleteData = {
            assignment_no: assignmentData.assignment_no,
            semester: currentSemester,
            course: currentCourse
        };
        
        // Add LMS-specific IDs if available (for actual LMS deletion)
        if (assignmentData.submission_id) {
            deleteData.submission_id = assignmentData.submission_id;
        }
        if (assignmentData.assignment_id) {
            deleteData.assignment_id = assignmentData.assignment_id;
        }
        if (assignmentData.offered_course_id) {
            deleteData.offered_course_id = assignmentData.offered_course_id;
        } else {
            deleteData.offered_course_id = btoa(currentCourse);
        }
        
        console.log('Deleting assignment with data:', deleteData);
        
        // Close modal and show loading
        closeDeleteModal();
        showLoadingOverlay('Deleting submission...');
        
        // Delete submission
        fetch('/lms/api/assignments/delete', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify(deleteData)
        })
        .then(response => response.json())
        .then(data => {
            hideLoadingOverlay();
            if (data.success) {
                // Show success banner and refresh
                showNotificationBanner('success', 'Submission Deleted', 'Your assignment submission has been removed.');
                // Refresh assignments
                setTimeout(() => {
                    loadAssignments(currentSemester, currentCourse, true);
                }, 500);
            } else {
                // Show error banner
                showNotificationBanner('error', 'Delete Failed', data.error || 'Unknown error occurred');
            }
        })
        .catch(error => {
            console.error('Delete error:', error);
            hideLoadingOverlay();
            // Show error banner
            showNotificationBanner('error', 'Delete Failed', 'Please check your connection and try again.');
        });
    };
    
    function hideAllStates() {
        updateRefreshVisibility();
        assignmentsContainer.style.display = 'none';
        emptyState.style.display = 'none';
        errorState.style.display = 'none';
    }
    
    function showEmptyState() {
        hideAllStates();
        // Reset to default "no course selected" message
        const title = emptyState.querySelector('.empty-state-title');
        const msg = emptyState.querySelector('.empty-state-message');
        if (title) title.textContent = 'No Course Selected';
        if (msg) msg.textContent = 'Select a course from the dropdown above to view your assignments';
        
        emptyState.classList.remove('animate-fade-in-scale');
        void emptyState.offsetWidth;
        emptyState.classList.add('animate-fade-in-scale');
        emptyState.style.display = 'flex';
    }
    
    function showError(message) {
        hideAllStates();
        errorMessage.textContent = message;
        
        errorState.classList.remove('animate-fade-in-scale');
        void errorState.offsetWidth;
        errorState.classList.add('animate-fade-in-scale');
        errorState.style.display = 'block';
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
        const overlay = document.getElementById('loadingOverlay');
        if (overlay) overlay.style.display = 'none';
        if (refreshButton) refreshButton.classList.remove('is-refreshing');
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

    // Keep old function names for upload/delete callbacks
    function showNotificationBanner(type, title, message) {
        showNotification(message, type, title);
    }
    window.showNotificationBanner = showNotificationBanner;
    window.showNotification = showNotification;
    
    // --- GROUP INVITES START ---
    window.showGroupInvitesModal = function() {
        if (typeof GROUPS_ENABLED === 'undefined' || !GROUPS_ENABLED) return;
        showLoadingOverlay('Loading your group invites...');
        
        fetch('/lms/api/groups/my_invites')
        .then(res => res.json())
        .then(data => {
            hideLoadingOverlay();
            if (!data.success) {
                alert(data.error || 'Failed to load group invites.');
                return;
            }
            
            const groups = data.groups || [];
            
            let tbodyHtml = '';
            if (groups.length === 0) {
                tbodyHtml = '<tr><td colspan="4" class="text-center" style="padding: 2rem;">You do not have any active group invites.</td></tr>';
            } else {
                groups.forEach(g => {
                    const dt = new Date(g.created_at);
                    const dateStr = dt.toLocaleDateString();
                    
                    let statusBadge = '';
                    if (g.status === 'Accepted') statusBadge = '<span class="badge bg-success">Accepted</span>';
                    else if (g.status === 'Declined') statusBadge = '<span class="badge bg-danger">Declined</span>';
                    else statusBadge = '<span class="badge bg-warning text-dark">Pending</span>';
                    
                    let actionsHtml = '';
                    if (g.status === 'Pending') {
                        actionsHtml = `
                            <button class="btn btn-sm btn-success me-1" onclick="respondGroupInvite('${g.group_id}', 'accept')" style="padding: 0.2rem 0.5rem; font-size: 0.75rem;"><i class="fa fa-check"></i> Accept</button>
                            <button class="btn btn-sm btn-danger" onclick="respondGroupInvite('${g.group_id}', 'decline')" style="padding: 0.2rem 0.5rem; font-size: 0.75rem;"><i class="fa fa-times"></i> Decline</button>
                        `;
                    } else if (g.status === 'Accepted') {
                        actionsHtml = `
                            <button class="btn btn-sm btn-outline-danger" onclick="respondGroupInvite('${g.group_id}', 'decline')" style="padding: 0.2rem 0.5rem; font-size: 0.75rem;"><i class="fa fa-sign-out-alt"></i> Withdraw</button>
                        `;
                    } else {
                        actionsHtml = `<span class="text-muted" style="font-size: 0.8rem;">No actions available</span>`;
                    }
                    
                    const others = g.partners.filter(p => p !== g.owner);
                    const partnersText = others.length > 2 ? `${others.slice(0, 2).join(', ')} +${others.length - 2} more` : others.join(', ');
                    
                    tbodyHtml += `
                        <tr>
                            <td><small>${dateStr}</small></td>
                            <td><strong>${g.owner}</strong><br/><small class="text-muted">Partners: ${partnersText || 'None'}</small></td>
                            <td>${statusBadge}</td>
                            <td>${actionsHtml}</td>
                        </tr>
                    `;
                });
            }
            
            const modalHtml = `
                <div class="modal-overlay" id="invitesModal" style="position: fixed; top: 0; left: 0; right: 0; bottom: 0; background: rgba(0,0,0,0.6); display: flex; align-items: center; justify-content: center; z-index: 9999; animation: fadeIn 0.2s ease-out;">
                    <div class="modal-container" style="background: var(--bg-primary); border-radius: var(--border-radius-lg); box-shadow: 0 20px 60px rgba(0,0,0,0.3); max-width: 700px; width: 90%; max-height: 80vh; overflow: hidden; animation: slideUp 0.3s ease-out; display: flex; flex-direction: column;">
                        <div class="modal-header" style="background: linear-gradient(135deg, #17a2b8, #117a8b); color: white; padding: 1.5rem; display: flex; justify-content: space-between; align-items: center;">
                            <h3 style="margin: 0; font-weight: 600; font-size: 1.25rem;"><i class="fa fa-envelope-open-text" style="margin-right: 0.5rem;"></i>My Group Invites</h3>
                            <button type="button" onclick="closeInvitesModal()" style="background: rgba(255,255,255,0.2); border: 0; color: white; font-size: 1.5rem; cursor: pointer; padding: 0.25rem 0.5rem; border-radius: 4px; transition: var(--transition); line-height: 1;" onmouseover="this.style.background='rgba(255,255,255,0.3)'" onmouseout="this.style.background='rgba(255,255,255,0.2)'">&times;</button>
                        </div>
                        <div class="modal-body" style="padding: 1.5rem; overflow-y: auto;">
                            <div class="table-responsive">
                                <table class="table table-hover">
                                    <thead class="table-light">
                                        <tr>
                                            <th>Created</th>
                                            <th>Group Details</th>
                                            <th>My Status</th>
                                            <th>Action</th>
                                        </tr>
                                    </thead>
                                    <tbody>
                                        ${tbodyHtml}
                                    </tbody>
                                </table>
                            </div>
                        </div>
                    </div>
                </div>
                <style>
                    @keyframes fadeIn { from { opacity: 0; } to { opacity: 1; } }
                    @keyframes fadeOut { from { opacity: 1; } to { opacity: 0; } }
                    @keyframes slideUp { from { transform: translateY(20px); opacity: 0; } to { transform: translateY(0); opacity: 1; } }
                </style>
            `;
            
            const existingModal = document.getElementById('invitesModal');
            if (existingModal) existingModal.remove();
            
            document.body.insertAdjacentHTML('beforeend', modalHtml);
            document.body.style.overflow = 'hidden';
        })
        .catch(err => {
            console.error('Error fetching group invites:', err);
            hideLoadingOverlay();
            alert('A network error occurred.');
        });
    };
    
    window.closeInvitesModal = function() {
        const modal = document.getElementById('invitesModal');
        if (modal) {
            modal.style.animation = 'fadeOut 0.2s ease-out';
            setTimeout(() => {
                modal.remove();
                document.body.style.overflow = '';
            }, 200);
        }
    };
    
    window.respondGroupInvite = function(groupId, action) {
        if (action === 'decline' && !confirm("Are you sure you want to decline/withdraw from this group? You will no longer be included in their submissions.")) {
            return;
        }
        
        showLoadingOverlay('Updating your status...');
        
        fetch(`/lms/api/groups/${groupId}/respond`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ action: action })
        })
        .then(res => res.json())
        .then(data => {
            hideLoadingOverlay();
            if (data.success) {
                if (data.message && data.message.includes('WARNING')) {
                    alert(data.message);
                } else {
                    showNotificationBanner('success', 'Status Updated', data.message || 'You have responded to the invite.');
                }
                
                window.closeInvitesModal();
                setTimeout(() => window.showGroupInvitesModal(), 200);
            } else {
                showNotificationBanner('error', 'Update Failed', data.error || 'Server rejected the status update.');
            }
        })
        .catch(err => {
            console.error('Error updating status:', err);
            hideLoadingOverlay();
            alert('A network error occurred.');
        });
    };
    // --- GROUP INVITES END ---

});
