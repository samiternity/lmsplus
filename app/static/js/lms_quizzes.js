// Quiz browsing follows the LMS course-page flow. Downloads remain individual links.
document.addEventListener('DOMContentLoaded', function () {
    const byId = id => document.getElementById(id);
    const semesterSelect = byId('semesterSelect');
    const courseSelect = byId('courseSelect');
    const refreshButton = byId('refreshQuizzes');
    const status = byId('quizStatus');
    let semesterPicker = null;
    let coursePicker = null;
    let generation = 0;
    let controller = null;
    let pollTimer = null;
    let watchdog = null;
    let optionsLoaded = false;
    let retry = loadOptions;

    if (window.TomSelect) {
        const settings = {create: false, controlInput: null, maxOptions: null, dropdownParent: 'body'};
        semesterPicker = new TomSelect(semesterSelect, settings);
        coursePicker = new TomSelect(courseSelect, {...settings, placeholder: 'Select a course...', allowEmptyOption: false});
    }

    function visibility(id, visible, display = 'block') {
        byId(id).style.display = visible ? display : 'none';
    }

    function updateRefreshVisibility() {
        visibility('courseRefreshActions', Boolean(semesterSelect.value && courseSelect.value));
    }

    function showState(id) {
        for (const name of ['quizzesContainer', 'emptyState', 'errorState', 'connectionErrorState']) {
            visibility(name, name === id, name === 'quizzesContainer' ? 'block' : 'flex');
        }
        updateRefreshVisibility();
    }

    function announce(message = '') {
        status.textContent = message;
        status.style.display = message ? 'block' : 'none';
    }

    function stopBusy() {
        visibility('loadingOverlay', false);
        refreshButton.classList.remove('is-refreshing');
        refreshButton.disabled = false;
        refreshButton.setAttribute('aria-busy', 'false');
        clearTimeout(watchdog);
        window.hideLMSBanner?.();
    }

    function begin() {
        generation += 1;
        controller?.abort();
        controller = new AbortController();
        clearTimeout(pollTimer);
        stopBusy();
        announce();
        return generation;
    }

    function startBusy() {
        visibility('loadingOverlay', true, 'flex');
        refreshButton.disabled = true;
        refreshButton.classList.add('is-refreshing');
        refreshButton.setAttribute('aria-busy', 'true');
        watchdog = setTimeout(() => window.showLMSBanner?.(
            'LMS responding slowly', 'Waiting for university servers to answer.'), 6000);
    }

    async function request(path, params, token) {
        const url = new URL(path, window.location.origin);
        Object.entries(params).forEach(([key, value]) => url.searchParams.set(key, value));
        const response = await fetch(url, {signal: controller.signal, cache: 'no-store'});
        let data;
        try { data = await response.json(); }
        catch { throw new Error('LMS returned an invalid response. Please retry.'); }
        if (token !== generation) throw new DOMException('Selection changed', 'AbortError');
        if (data.survey_required || data.error === 'CMS_SURVEY_REQUIRED') {
            window.showSurveyModal?.();
            throw new Error('Complete the mandatory CMS surveys, then retry.');
        }
        if (!response.ok || data.success === false || data.error) {
            throw new Error(data.error || 'Unable to reach LMS. Please retry.');
        }
        return data;
    }

    function showError(error, initial = false) {
        const id = initial ? 'connectionErrorState' : 'errorState';
        byId(initial ? 'connectionErrorMessage' : 'errorMessage').textContent = error.message;
        showState(id);
    }

    function empty(title = 'No Course Selected', message = 'Select a course from the dropdown above to view quizzes.') {
        byId('emptyState').querySelector('.empty-state-title').textContent = title;
        byId('emptyState').querySelector('.empty-state-message').textContent = message;
        showState('emptyState');
    }

    function populate(select, picker, options, selected = '') {
        if (picker) {
            picker.clear(true);
            picker.clearOptions();
            picker.addOptions(options.map(option => ({value: option.code, text: option.name})));
            picker.setValue(selected, true);
            picker.refreshOptions(false);
        } else {
            select.replaceChildren();
            const placeholder = document.createElement('option');
            placeholder.value = '';
            placeholder.textContent = select === courseSelect ? 'Select a course...' : 'Select a semester...';
            select.appendChild(placeholder);
            for (const option of options) {
                const node = document.createElement('option');
                node.value = option.code;
                node.textContent = option.name;
                select.appendChild(node);
            }
            select.value = selected;
        }
    }

    function enableCourses(enabled) {
        courseSelect.disabled = !enabled;
        if (coursePicker) enabled ? coursePicker.enable() : coursePicker.disable();
    }

    function resetCourseSelection() {
        populate(courseSelect, coursePicker, []);
        enableCourses(false);
        byId('quizzesTableBody').replaceChildren();
        empty();
    }

    function step(index) {
        const steps = ['step-connect', 'step-courses', 'step-quizzes', 'step-done'];
        steps.forEach((id, i) => {
            byId(id).classList.toggle('active', i === index);
            byId(id).classList.toggle('done', i < index);
        });
        byId('unifiedProgressBar').style.width = [15, 45, 75, 100][index] + '%';
        byId('loadingStatusText').textContent = ['Connecting to LMS...', 'Fetching semesters...',
            'Loading courses...', 'Ready'][index];
    }

    async function loadOptions() {
        const token = begin();
        retry = loadOptions;
        visibility('initialLoadingState', true, 'flex');
        visibility('mainContent', false);
        visibility('connectionErrorState', false);
        resetCourseSelection();
        step(0);
        try {
            const data = await request('/lms/api/quizzes/options', {}, token);
            if (token !== generation) return;
            if (!data.semesters?.length) throw new Error('LMS returned no semesters. Please retry.');
            step(1);
            const semester = data.semesters[0].code;
            populate(semesterSelect, semesterPicker, data.semesters, semester);
            step(2);
            // Fetch courses for the selected semester instead of assuming the initial page matches it.
            const courses = await request('/lms/api/quizzes/courses', {semester}, token);
            if (token !== generation) return;
            populate(courseSelect, coursePicker, courses.courses || []);
            enableCourses(Boolean(courses.courses?.length));
            optionsLoaded = true;
            step(3);
            visibility('mainContent', true);
            if (!courses.courses?.length) empty('No Courses Found', 'No courses were found for this semester.');
        } catch (error) {
            if (token === generation && error.name !== 'AbortError') showError(error, true);
        } finally {
            if (token === generation) {
                visibility('initialLoadingState', false);
                stopBusy();
            }
        }
    }

    async function loadCourses() {
        const token = begin();
        const semester = semesterSelect.value;
        retry = loadCourses;
        resetCourseSelection();
        if (!semester) return;
        startBusy();
        try {
            const data = await request('/lms/api/quizzes/courses', {semester}, token);
            if (token !== generation) return;
            populate(courseSelect, coursePicker, data.courses || []);
            enableCourses(Boolean(data.courses?.length));
            if (!data.courses?.length) empty('No Courses Found', 'No courses were found for this semester.');
        } catch (error) {
            if (token === generation && error.name !== 'AbortError') showError(error);
        } finally {
            if (token === generation) stopBusy();
        }
    }

    function createQuizRow(quiz) {
        const row = document.createElement('tr');
        for (const [index, value] of [quiz.quiz_no, quiz.title, quiz.quiz_file, quiz.marks, quiz.solution_file].entries()) {
            const cell = document.createElement('td');
            cell.className = 'px-4 py-3 ' + (index === 1 ? 'text-start' : 'text-center');
            if ((index === 2 || index === 4) && value) {
                const link = document.createElement('a');
                link.className = 'subtle-download-btn btn-soft-primary';
                link.href = '/lms/download/' + encodeURIComponent(value);
                const label = index === 2 ? 'Download quiz' : 'Download solution';
                link.setAttribute('aria-label', `${label}: ${quiz.title || 'Quiz ' + quiz.quiz_no}`);
                const icon = document.createElement('i');
                icon.className = 'fas fa-download';
                icon.setAttribute('aria-hidden', 'true');
                link.append(icon, document.createTextNode(label));
                cell.appendChild(link);
            } else {
                cell.textContent = value === null || value === undefined || value === '' ? '—' : String(value);
            }
            row.appendChild(cell);
        }
        return row;
    }

    function displayQuizzes(quizzes) {
        byId('quizzesTableBody').replaceChildren(...quizzes.map(createQuizRow));
        if (quizzes.length) showState('quizzesContainer');
        else empty('No Quizzes Found', 'No quizzes were found for this course.');
    }

    async function pollRefresh(taskId, token, deadline = Date.now() + 180000) {
        pollTimer = setTimeout(async () => {
            if (token !== generation) return;
            try {
                const data = await request('/lms/api/refresh-status', {task_id: taskId}, token);
                if (token !== generation) return;
                if (data.done) {
                    if (data.data !== null) displayQuizzes(data.data);
                    announce('Quizzes updated.');
                } else if (Date.now() < deadline) pollRefresh(taskId, token, deadline);
                else announce('Refresh timed out. Showing saved quizzes. Use Refresh to retry.');
            } catch (error) {
                if (token === generation && error.name !== 'AbortError') {
                    announce(error.message + ' Showing saved quizzes. Use Refresh to retry.');
                }
            }
        }, 3000);
    }

    async function loadQuizzes(forceRefresh = false) {
        const token = begin();
        retry = () => loadQuizzes(true);
        const semester = semesterSelect.value;
        const course = courseSelect.value;
        updateRefreshVisibility();
        if (!semester || !course) { empty(); return; }
        showState(null);
        startBusy();
        try {
            const path = '/lms/api/quizzes/' + (forceRefresh ? 'fetch' : 'data');
            const data = await request(path, {semester, course, refresh: String(forceRefresh)}, token);
            if (token !== generation) return;
            displayQuizzes(data.quizzes || []);
            if (data.is_stale && data.task_id) {
                announce('Showing saved quizzes. Refreshing from LMS...');
                pollRefresh(data.task_id, token);
            }
        } catch (error) {
            if (token === generation && error.name !== 'AbortError') showError(error);
        } finally {
            if (token === generation) stopBusy();
        }
    }

    semesterSelect.addEventListener('change', loadCourses);
    courseSelect.addEventListener('change', () => loadQuizzes());
    refreshButton.addEventListener('click', () => loadQuizzes(true));
    byId('retryButton').addEventListener('click', () => retry());
    byId('connectionRetryButton').addEventListener('click', loadOptions);
    window.addEventListener('pagehide', () => { begin(); });
    enableCourses(false);
    if (!optionsLoaded) loadOptions();
});
