window.smacxAccess = {
  takeInvitationFragment: function () {
    const value = new URLSearchParams(window.location.hash.replace(/^#/, '')).get('invite') || '';
    if (value) history.replaceState(null, document.title, window.location.pathname + window.location.search);
    return value;
  },
  hashInstallation: async function (inputId, candidates) {
    const input = document.getElementById(inputId);
    if (!input || !input.files || input.files.length === 0) throw new Error('Choose the folder containing your SMAC or Alien Crossfire installation.');
    const files = Array.from(input.files);
    const normalize = value => value.replace(/\\/g, '/').replace(/^\.\//, '').toLowerCase();
    const evidence = [];
    for (const candidate of candidates) {
      const paths = (candidate.relativePaths || []).map(normalize);
      const file = files.find(item => {
        const relative = normalize(item.webkitRelativePath || item.name);
        return paths.some(path => relative === path || relative.endsWith('/' + path));
      });
      if (!file) continue;
      if (candidate.expectedMinimumSize != null && file.size < candidate.expectedMinimumSize) continue;
      if (candidate.expectedMaximumSize != null && file.size > candidate.expectedMaximumSize) continue;
      const digest = await crypto.subtle.digest('SHA-256', await file.arrayBuffer());
      const sha256 = Array.from(new Uint8Array(digest), byte => byte.toString(16).padStart(2, '0')).join('');
      evidence.push({ id: candidate.id, size: file.size, sha256 });
    }
    if (evidence.length === 0) throw new Error('No recognizable Alpha Centauri files were found in that folder.');
    return evidence;
  }
};
