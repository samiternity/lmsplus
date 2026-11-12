// Regression checks for recovery controls on both course pages.
const {readFileSync} = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');

function extract(source, name) {
    const start = source.indexOf(`    function ${name}(`);
    assert(start >= 0, name);
    const next = source.indexOf('\n    function ', start + 1);
    return source.slice(start, next < 0 ? undefined : next);
}

(async () => {
    for (const [page, containerName, refreshId] of [
        ['assignments', 'assignmentsContainer', 'refreshAssignments'],
        ['lecture_notes', 'lectureNotesContainer', 'refreshLectureNotes']
    ]) {
        const source = readFileSync(`app/static/js/lms_${page}_new.js`, 'utf8');
        const template = readFileSync(`app/templates/lms_${page}_new.html`, 'utf8');
        assert(!source.includes("text: 'Loading courses...'"));
        assert(template.indexOf(`id="${refreshId}"`) < template.indexOf(`id="${containerName}"`));
        const actions = {style: {}};
        const course = {value: 'old-course', disabled: false};
        const semester = {value: 'Fall-2026'};
        // Model Tom Select's preservation of selected options in clearOptions().
        const tom = {
            options: {'old-course': {text: 'Old course'}},
            clear() { course.value = ''; },
            clearOptions() {
                this.options = Object.fromEntries(Object.entries(this.options).filter(([key]) => key === course.value));
            },
            addOption(option) { this.options[option.value] = option; },
            setValue(value) { course.value = value; },
            refreshOptions() {}, sync() {}, disable() {}, enable() {}
        };
        const context = vm.createContext({
            console: {log() {}, error() {}}, URL, window: {location: {origin: 'http://localhost'}},
            document: {getElementById: () => actions}, semesterSelect: semester, courseSelect: course,
            lectureDownloadRequest: 0, tsCourse: tom, currentSemester: 'Fall-2026', currentCourse: 'old-course',
            [containerName]: {style: {}}, emptyState: {style: {}}, errorState: {style: {}},
            setLectureDownloadMotion() {}, downloadAllBtn: {style: {}}, showLoadingOverlay() {}, hideLoadingOverlay() {},
            startLMSWatchdog() {}, clearLMSWatchdog() {}, showNotification() {},
            fetch: async () => ({ok: true, json: async () => ({success: true, courses: [{code: 'new-course', name: 'New course'}]})})
        });
        for (const name of ['updateRefreshVisibility', 'hideAllStates', 'resetCourseSelection',
                            'populateCourseDropdown', 'updateCoursesForSemester']) {
            vm.runInContext(extract(source, name), context);
        }
        context.showEmptyState = () => context.hideAllStates();
        context.hideAllStates();
        assert.equal(actions.style.display, 'block', `${page}: refresh survives hidden table/error/empty state`);
        course.value = '';
        context.hideAllStates();
        assert.equal(actions.style.display, 'none');
        course.value = 'old-course';
        context.resetCourseSelection();
        assert.equal(course.value, '');
        assert(!tom.options['old-course']);
        assert.equal(actions.style.display, 'none');
        context.updateCoursesForSemester('Fall-2026');
        await new Promise(resolve => setImmediate(resolve));
        assert(tom.options['new-course']);
        assert(!Object.values(tom.options).some(o => /Loading courses/.test(o.text)));
        course.value = 'new-course';
        context.hideAllStates();
        assert.equal(actions.style.display, 'block');
        semester.value = '';
        context.hideAllStates();
        assert.equal(actions.style.display, 'none');
        console.log(`${page}: dropdown reset and refresh visibility checks passed`);
    }
})().catch(error => { console.error(error); process.exitCode = 1; });
