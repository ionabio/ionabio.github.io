(() => {
  const key = 'savingsTrackerNabi';
  const status = document.getElementById('transferStatus');
  const say = message => { status.textContent = message; };
  function valid(value) {
    return value && typeof value === 'object' && ['car','house'].every(k => typeof value[k] === 'number' && Number.isFinite(value[k]) && value[k] >= 0 && value[k] <= 1e12);
  }
  document.getElementById('exportProgress').addEventListener('click', () => {
    try {
      const raw = localStorage.getItem(key);
      if (!raw) return say('No saved progress was found in this browser. Try the browser and profile where you used the old tracker.');
      const values = JSON.parse(raw);
      if (!valid(values)) return say('Saved progress could not be read safely. Your original storage is unchanged.');
      const blob = new Blob([JSON.stringify({schema: 1, progress: {car: values.car, house: values.house}})], {type:'application/json'});
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a'); a.href = url; a.download = 'nabi-savings-progress.json'; a.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
      say('Download created locally. It contains your progress; keep it private. Your original saved data is unchanged.');
    } catch (_) { say('Unable to export in this browser. Your original storage is unchanged.'); }
  });
  const file = document.getElementById('importProgress');
  if (file) file.addEventListener('change', async () => {
    try {
      const selected = file.files[0];
      if (!selected || selected.size > 4096) throw new Error();
      const parsed = JSON.parse(await selected.text());
      const values = parsed.schema === 1 ? parsed.progress : parsed;
      if (!valid(values)) throw new Error();
      if (!window.confirm('Replace the saved progress in this browser with this private file? Your old tracker data will remain intact.')) return;
      const previous = localStorage.getItem(key);
      if (previous) localStorage.setItem(key + 'BeforeImport', previous);
      localStorage.setItem(key, JSON.stringify({car: values.car, house: values.house}));
      window.location.reload();
    } catch (_) { say('This file is not valid savings progress. Nothing was changed.'); }
  });
})();