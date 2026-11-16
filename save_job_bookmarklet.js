(function () {
  var d = document, j = null;
  d.querySelectorAll('script[type="application/ld+json"]').forEach(function (s) {
    try {
      var o = JSON.parse(s.textContent);
      (Array.isArray(o) ? o : (o['@graph'] || [o])).forEach(function (x) { if (x && x['@type'] === 'JobPosting') j = x; });
    } catch (e) {}
  });
  var m = function (p) { var e = d.querySelector('meta[property="' + p + '"]'); return e ? e.content : ''; };
  var out = {
    _job: 1,
    title: (j && j.title) || m('og:title') || d.title,
    employer: (j && j.hiringOrganization && j.hiringOrganization.name) || m('og:site_name') || location.hostname.replace(/^www\./, ''),
    deadline: String((j && j.validThrough) || '').slice(0, 10),
    url: location.href.split('#')[0]
  };
  window.__savedJob = out;
  var t = JSON.stringify(out);
  var ok = function () { alert('Copied: ' + out.title + '\n\nNow paste it into the finder (My applications, Add a position).'); };
  var fb = function () { prompt('Copy this, then paste it into the finder:', t); };
  if (navigator.clipboard && navigator.clipboard.writeText) { navigator.clipboard.writeText(t).then(ok, fb); } else { fb(); }
})();
