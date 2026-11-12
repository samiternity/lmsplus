/**
 * LMS Lecture Notes JavaScript
 * Handles lecture notes page functionality including view switching, 
 * course section toggling, file preview, and filtering
 */

// Global variables
let currentView = 'course';
let expandedSections = new Set();

// Initialize when DOM is loaded
document.addEventListener('DOMContentLoaded', function() {
    initializeLectureNotes();
});

function initializeLectureNotes() {
    // Initialize view toggle functionality
    initializeViewToggle();
    
    // Initialize filter functionality
    initializeFilters();
    
    // Initialize course section toggles
    initializeCourseSections();
    
    // Initialize file actions
    initializeFileActions();
    
    // Restore saved preferences
    restorePreferences();
}

function initializeViewToggle() {
    const viewButtons = document.querySelectorAll('.view-btn');
    viewButtons.forEach(btn => {
        btn.addEventListener('click', function() {
            const view = this.dataset.view;
            switchView(view);
        });
    });
}

function initializeFilters() {
    // Apply filters button
    const applyBtn = document.getElementById('applyFilters');
    if (applyBtn) {
        applyBtn.addEventListener('click', applyFilters);
    }
    
    // Clear filters button
    const clearBtn = document.getElementById('clearFilters');
    if (clearBtn) {
        clearBtn.addEventListener('click', clearFilters);
    }
    
    // Refresh button
    const refreshBtn = document.getElementById('refreshNotes');
    if (refreshBtn) {
        refreshBtn.addEventListener('click', refreshLectureNotes);
    }
    
    // Sort dropdown
    const sortSelect = document.getElementById('sortBy');
    if (sortSelect) {
        sortSelect.addEventListener('change', applySorting);
    }
    
    // Search input with debounce
    const searchInput = document.getElementById('searchInput');
    if (searchInput) {
        let searchTimeout;
        searchInput.addEventListener('input', function() {
            clearTimeout(searchTimeout);
            searchTimeout = setTimeout(applyFilters, 500);
        });
    }
}

function initializeCourseSections() {
    // Auto-expand all sections by default
    const courseSections = document.querySelectorAll('.course-section');
    courseSections.forEach((section, index) => {
        const sectionId = index + 1;
        expandedSections.add(sectionId);
    });
}

function initializeFileActions() {
    // Details buttons
    document.querySelectorAll('.details-btn').forEach(btn => {
        btn.addEventListener('click', function() {
            const note = JSON.parse(this.dataset.note);
            openNoteDetailsModal(note);
        });
    });
    
    // Download all buttons
    document.querySelectorAll('.download-all-btn').forEach(btn => {
        btn.addEventListener('click', function() {
            downloadAllFiles(this.dataset.noteId, this.dataset.course);
        });
    });
    
    // Preview buttons
    document.querySelectorAll('.preview-btn').forEach(btn => {
        btn.addEventListener('click', function() {
            previewFile(this.dataset.fileUrl);
        });
    });
}

function restorePreferences() {
    // Restore view preference
    const savedView = localStorage.getItem('lectureNotesView') || 'course';
    switchView(savedView);
    
    // Restore expanded sections
    const savedSections = localStorage.getItem('lectureNotesExpandedSections');
    if (savedSections) {
        expandedSections = new Set(JSON.parse(savedSections));
        updateSectionVisibility();
    }
}

function switchView(view) {
    const views = ['list', 'card', 'course'];
    const buttons = {
        list: document.getElementById('listView'),
        card: document.getElementById('cardView'),
        course: document.getElementById('courseView')
    };
    const containers = {
        list: document.getElementById('lectureNotesList'),
        card: document.getElementById('lectureNotesCards'),
        course: document.getElementById('lectureNotesCourse')
    };
    
    // Update buttons
    views.forEach(v => {
        if (buttons[v]) {
            buttons[v].classList.toggle('active', v === view);
        }
    });
    
    // Update containers
    views.forEach(v => {
        if (containers[v]) {
            containers[v].style.display = v === view ? 'block' : 'none';
        }
    });
    
    currentView = view;
    localStorage.setItem('lectureNotesView', view);
}

function toggleCourseSection(sectionId) {
    const courseContent = document.getElementById(`courseContent${sectionId}`);
    const courseToggle = courseContent.parentElement.querySelector('.course-toggle i');
    
    if (expandedSections.has(sectionId)) {
        // Collapse section
        expandedSections.delete(sectionId);
        courseContent.classList.add('collapsed');
        courseToggle.classList.remove('expanded');
    } else {
        // Expand section
        expandedSections.add(sectionId);
        courseContent.classList.remove('collapsed');
        courseToggle.classList.add('expanded');
    }
    
    // Save state
    localStorage.setItem('lectureNotesExpandedSections', JSON.stringify([...expandedSections]));
}

function updateSectionVisibility() {
    expandedSections.forEach(sectionId => {
        const courseContent = document.getElementById(`courseContent${sectionId}`);
        const courseToggle = courseContent?.parentElement?.querySelector('.course-toggle i');
        
        if (courseContent && courseToggle) {
            courseContent.classList.remove('collapsed');
            courseToggle.classList.add('expanded');
        }
    });
}

function previewFile(fileUrl) {
    // Create preview modal
    const previewModal = document.createElement('div');
    previewModal.className = 'modal-overlay';
    previewModal.innerHTML = `
        <div class="modal-content modal-large">
            <div class="modal-header">
                <h3>File Preview</h3>
                <button class="modal-close" onclick="closePreviewModal()">
                    <i class="fas fa-times"></i>
                </button>
            </div>
            <div class="modal-body">
                <div class="preview-container">
                    <iframe src="${fileUrl}" width="100%" height="600px" frameborder="0">
                        <p>Your browser does not support iframes. <a href="${fileUrl}" target="_blank">Click here to view the file</a>.</p>
                    </iframe>
                </div>
            </div>
            <div class="modal-footer">
                <a href="${fileUrl}" class="btn btn-primary" target="_blank">
                    <i class="fas fa-external-link-alt"></i> Open in New Tab
                </a>
                <button class="btn btn-secondary" onclick="closePreviewModal()">Close</button>
            </div>
        </div>
    `;
    
    previewModal.id = 'previewModal';
    document.body.appendChild(previewModal);
    previewModal.style.display = 'flex';
    
    // Close on background click
    previewModal.addEventListener('click', function(e) {
        if (e.target === previewModal) {
            closePreviewModal();
        }
    });
}

function closePreviewModal() {
    const previewModal = document.getElementById('previewModal');
    if (previewModal) {
        previewModal.remove();
    }
}

function applyFilters() {
    const semester = document.getElementById('semesterFilter')?.value || '';
    const course = document.getElementById('courseFilter')?.value || '';
    const search = document.getElementById('searchInput')?.value || '';
    const sort = document.getElementById('sortBy')?.value || '';
    
    const params = new URLSearchParams();
    if (semester) params.append('semester', semester);
    if (course) params.append('course', course);
    if (search) params.append('search', search);
    if (sort) params.append('sort', sort);
    
    // Get the current URL and update with new parameters
    const baseUrl = window.location.pathname;
    const newUrl = params.toString() ? `${baseUrl}?${params.toString()}` : baseUrl;
    window.location.href = newUrl;
}

function clearFilters() {
    // Clear all filter inputs
    const semesterFilter = document.getElementById('semesterFilter');
    const courseFilter = document.getElementById('courseFilter');
    const searchInput = document.getElementById('searchInput');
    const sortBy = document.getElementById('sortBy');
    
    if (semesterFilter) semesterFilter.value = '';
    if (courseFilter) courseFilter.value = '';
    if (searchInput) searchInput.value = '';
    if (sortBy) sortBy.value = 'week_desc';
    
    // Redirect to base URL
    window.location.href = window.location.pathname;
}

function applySorting() {
    applyFilters();
}

function refreshLectureNotes() {
    const btn = document.getElementById('refreshNotes');
    if (!btn) return;
    
    const originalContent = btn.innerHTML;
    
    btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Refreshing...';
    btn.disabled = true;
    
    // Add refresh parameter to current URL
    const params = new URLSearchParams(window.location.search);
    params.set('refresh', 'true');
    
    fetch(`${window.location.pathname}?${params.toString()}`, {
        method: 'GET',
        headers: {
            'X-Requested-With': 'XMLHttpRequest'
        }
    })
    .then(response => {
        if (response.ok) {
            showFlashMessage('Lecture notes refreshed successfully!', 'success');
            setTimeout(() => window.location.reload(), 1000);
        } else {
            throw new Error('Refresh failed');
        }
    })
    .catch(error => {
        console.error('Error:', error);
        showFlashMessage('Failed to refresh lecture notes', 'error');
    })
    .finally(() => {
        btn.innerHTML = originalContent;
        btn.disabled = false;
    });
}

function downloadAllFiles(noteId, courseCode) {
    // Show download progress modal
    const progressModal = document.getElementById('downloadProgressModal');
    const statusElement = document.getElementById('downloadStatus');
    const progressBar = document.getElementById('downloadProgressBar');
    const downloadedCount = document.getElementById('downloadedCount');
    const totalCount = document.getElementById('totalCount');
    
    if (!progressModal) return;
    
    progressModal.style.display = 'flex';
    statusElement.textContent = 'Preparing download...';
    progressBar.style.width = '0%';
    
    // Make download request
    fetch('/lms/download-all-files', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
        },
        body: JSON.stringify({
            note_id: noteId,
            course_code: courseCode
        })
    })
    .then(response => response.json())
    .then(data => {
        if (data.success) {
            // Simulate progress updates
            let progress = 0;
            const total = data.file_count || 1;
            totalCount.textContent = total;
            
            const progressInterval = setInterval(() => {
                progress += 1;
                downloadedCount.textContent = progress;
                const percentage = (progress / total) * 100;
                progressBar.style.width = percentage + '%';
                statusElement.textContent = `Downloading file ${progress} of ${total}...`;
                
                if (progress >= total) {
                    clearInterval(progressInterval);
                    statusElement.textContent = 'Download complete!';
                    setTimeout(() => {
                        progressModal.style.display = 'none';
                        if (data.download_url) {
                            window.open(data.download_url, '_blank');
                        }
                    }, 1000);
                }
            }, 500);
        } else {
            statusElement.textContent = 'Download failed: ' + (data.error || 'Unknown error');
            setTimeout(() => {
                progressModal.style.display = 'none';
            }, 2000);
        }
    })
    .catch(error => {
        console.error('Error:', error);
        statusElement.textContent = 'Network error occurred';
        setTimeout(() => {
            progressModal.style.display = 'none';
        }, 2000);
    });
    
    // Cancel download functionality
    const cancelBtn = document.getElementById('cancelDownload');
    if (cancelBtn) {
        cancelBtn.onclick = function() {
            progressModal.style.display = 'none';
        };
    }
}

function openNoteDetailsModal(note) {
    const modal = document.getElementById('noteDetailsModal');
    const detailsContainer = document.getElementById('noteDetailsContent');
    
    if (!modal || !detailsContainer) return;
    
    let materialsHtml = '';
    
    // Generate materials HTML (same as in template)
    if (note.lecture_files && note.lecture_files.length > 0) {
        materialsHtml += generateMaterialGroupHtml('Lecture Files', note.lecture_files, 'file-alt', 'primary');
    }
    
    if (note.video_links && note.video_links.length > 0) {
        materialsHtml += generateMaterialGroupHtml('Video Links', note.video_links, 'video', 'warning');
    }
    
    if (note.supplementary_links && note.supplementary_links.length > 0) {
        materialsHtml += generateMaterialGroupHtml('Supplementary Links', note.supplementary_links, 'external-link-alt', 'success');
    }
    
    detailsContainer.innerHTML = `
        <div class="note-details">
            <div class="detail-section">
                <h4>Lecture Information</h4>
                <div class="detail-grid">
                    <div class="detail-item">
                        <label>Week Number:</label>
                        <span>Week ${note.week_no}</span>
                    </div>
                    <div class="detail-item">
                        <label>Title:</label>
                        <span>${note.title}</span>
                    </div>
                    <div class="detail-item">
                        <label>Course:</label>
                        <span>${note.course_name} (${note.course_code})</span>
                    </div>
                    ${note.upload_date ? `
                    <div class="detail-item">
                        <label>Upload Date:</label>
                        <span>${new Date(note.upload_date).toLocaleDateString('en-US', {
                            weekday: 'long',
                            year: 'numeric',
                            month: 'long',
                            day: 'numeric'
                        })}</span>
                    </div>
                    ` : ''}
                </div>
                
                ${note.remarks ? `
                <div style="margin-top: 1rem;">
                    <label style="display: block; margin-bottom: 0.5rem; font-size: 0.75rem; font-weight: 600; color: var(--text-muted); text-transform: uppercase;">Remarks:</label>
                    <div style="background-color: var(--bg-primary); padding: 1rem; border-radius: var(--border-radius); border: 1px solid var(--border-color);">
                        <p style="margin: 0; color: var(--text-secondary);">${note.remarks}</p>
                    </div>
                </div>
                ` : ''}
            </div>
            
            ${materialsHtml ? `
            <div class="detail-section">
                <h4>Materials</h4>
                <div class="materials-list">
                    ${materialsHtml}
                </div>
            </div>
            ` : ''}
        </div>
    `;
    
    modal.style.display = 'flex';
}

function generateMaterialGroupHtml(title, items, icon, buttonClass) {
    return `
        <div class="material-group">
            <div class="material-group-header">
                <i class="fas fa-${icon}"></i>
                ${title} (${items.length})
            </div>
            <div class="material-group-content">
                <div class="material-links">
                    ${items.map(item => `
                        <div class="material-link">
                            <div class="material-link-info">
                                <div class="material-link-icon">
                                    <i class="fas fa-${icon}"></i>
                                </div>
                                <div class="material-link-details">
                                    <div class="material-link-name">${getFileName(item)}</div>
                                    <div class="material-link-type">${title.slice(0, -1)}</div>
                                </div>
                            </div>
                            <div class="material-link-actions">
                                <a href="${icon === 'file-alt' ? '/lms/download/' + encodeURIComponent(item) : item}" class="btn btn-${buttonClass} btn-sm" target="_blank">
                                    <i class="fas fa-${icon === 'file-alt' ? 'download' : icon === 'video' ? 'play' : 'external-link-alt'}"></i>
                                </a>
                            </div>
                        </div>
                    `).join('')}
                </div>
            </div>
        </div>
    `;
}

function closeNoteDetailsModal() {
    const modal = document.getElementById('noteDetailsModal');
    if (modal) {
        modal.style.display = 'none';
    }
}

function getFileName(url) {
    try {
        const urlObj = new URL(url);
        const pathname = urlObj.pathname;
        const filename = pathname.split('/').pop();
        return filename || 'Unknown File';
    } catch (e) {
        return 'Unknown File';
    }
}

function showFlashMessage(message, type) {
    // Try to find existing flash container
    let flashContainer = document.querySelector('.flash-messages');
    
    // If no container exists, create one
    if (!flashContainer) {
        flashContainer = document.createElement('div');
        flashContainer.className = 'flash-messages';
        document.body.insertBefore(flashContainer, document.body.firstChild);
    }
    
    const flashMessage = document.createElement('div');
    flashMessage.className = `flash-message ${type}`;
    flashMessage.textContent = message;
    
    flashContainer.appendChild(flashMessage);
    
    setTimeout(() => {
        flashMessage.remove();
    }, 5000);
}

// Export functions for global access
window.toggleCourseSection = toggleCourseSection;
window.previewFile = previewFile;
window.closePreviewModal = closePreviewModal;
window.closeNoteDetailsModal = closeNoteDetailsModal;
window.downloadAllFiles = downloadAllFiles;
window.openNoteDetailsModal = openNoteDetailsModal;