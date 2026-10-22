const fs = require('node:fs'), path = require('node:path'), crypto = require('node:crypto');
const { spawnSync } = require('node:child_process');
const QT_NOTICE_PROVENANCE = 'LICENSES/qt-third-party/PROVENANCE.json';
const QT_NOTICE_PROVENANCE_SHA256 = '124cf0986d1166930f5564437c2facfacf9326b722a456563757b9c016d13c82';
let reviewedQtNoticeHashes;
function isReviewedQtNotice(name, bytes) {
  if (!/^LICENSES\/qt-third-party\/[^/]+\.txt$/.test(name)) return false;
  if (!reviewedQtNoticeHashes) {
    const provenance = fs.readFileSync(path.resolve(__dirname, '..', QT_NOTICE_PROVENANCE));
    if (crypto.createHash('sha256').update(provenance).digest('hex') !== QT_NOTICE_PROVENANCE_SHA256) throw Error('Unreviewed Qt notice provenance');
    reviewedQtNoticeHashes = new Map(JSON.parse(provenance.toString('utf8')).files.map(entry =>
      ['LICENSES/qt-third-party/' + entry.file, entry.sha256]));
  }
  return reviewedQtNoticeHashes.get(name) === crypto.createHash('sha256').update(bytes).digest('hex');
}
function inspectFile(name, bytes) {
  if (!name || name.includes('\\') || path.posix.isAbsolute(name) || name.split('/').some(x => !x || x === '..' || /^\.git$/i.test(x))) throw Error('Unsafe path: ' + name);
  if (/(?:^|\/)(?:assets|evidence|resources|user-assets|dist|dist-public|build|node_modules|\.venv|__pycache__|记录|参考资料|运行|图片|预览|logs)(?:\/|$)/i.test(name)
      || /(?:SNAPSHOT|PROGRAM-PACKAGE)\.json$/i.test(name)
      || /\.(?:png|webp|jpe?g|gif|ico|bmp|avif|mp[34]|wav|ogg|zip|7z|exe|dll|pyd|pyc|sqlite3?|db|log)$/i.test(name)) throw Error('Private/generated/art path: ' + name);
  const content = new TextDecoder('utf-8', { fatal: true }).decode(bytes);
  const historicalName=new RegExp(String.fromCharCode(107,111,116,111,110,101)+'|'+String.fromCharCode(29748,38899),'i');
  if(historicalName.test(name)||historicalName.test(content))throw Error('Character-specific name in generic public distribution: '+name);
  if (content.includes('\0')) throw Error('Binary input: ' + name);
  const checks = [
    /[A-Z]:[\\/]+Users[\\/]+(?!Public\b|Default\b|runneradmin\b)[^\\/\s"']+/i,
    /\/(?:Users|home)\/(?!runner\b)[a-z][\w.-]+\//i,
    /(?:github_pat_[A-Za-z0-9_]{30,}|gh[pousr]_[A-Za-z0-9]{25,}|sk-(?:proj-)?[A-Za-z0-9_-]{30,})/,
    /-----BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY-----/,
    /data:image\/[\w+.-]+;base64,[A-Za-z0-9+/]{128}/i,
    /https?:\/\/[^\s/@:]+:[^\s/@]+@/i,
  ];
  if (checks.some(pattern => pattern.test(content))) throw Error('Private path, credential or embedded artwork: ' + name);
  const emails = content.match(/[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}/g) || [];
  // Upstream authors' addresses are allowed only in byte-identical reviewed notices.
  // The pinned manifest hash prevents adding an arbitrary file to this exception.
  const reviewedQtNotice = emails.length > 0 && isReviewedQtNotice(name, bytes);
  for (const email of emails) if (!/@(?:example\.(?:com|org|invalid)|users\.noreply\.github\.com)$/i.test(email)
      && !(name.startsWith('LICENSES/') && /@(?:gnu\.org|fsf\.org|qt-project\.org)$/.test(email))
      && !reviewedQtNotice) throw Error('Unreviewed email address: ' + name);
}
function verify(root, { allFiles = false } = {}) {
  const manifestBytes=fs.readFileSync(path.join(root, 'PUBLIC-FILES.json'));
  inspectFile('PUBLIC-FILES.json',manifestBytes);
  const manifest = JSON.parse(manifestBytes.toString('utf8'));
  if (Object.keys(manifest).sort().join(',')!=='distribution,files,repository,schema' || manifest.schema !== 1 || manifest.distribution !== 'source-code-only'
      || manifest.repository!=='AlphaUntilReset/codex-tools' || !manifest.files || typeof manifest.files!=='object' || Array.isArray(manifest.files)) throw Error('Invalid public manifest');
  const expected = new Set([...Object.keys(manifest.files), 'PUBLIC-FILES.json']);
  for (const [name, hash] of Object.entries(manifest.files)) {
    if(name==='PUBLIC-FILES.json' || !/^[a-f0-9]{64}$/.test(hash))throw Error('Invalid inventory entry: '+name);
    inspectFile(name,Buffer.from(''));
    let current = root;
    for (const part of name.split('/')) { current = path.join(current, part); if (fs.lstatSync(current).isSymbolicLink()) throw Error('Linked file: ' + name); }
    const bytes = fs.readFileSync(current); inspectFile(name, bytes);
    if (crypto.createHash('sha256').update(bytes).digest('hex') !== hash) throw Error('Unreviewed file change: ' + name);
  }
  const git = spawnSync('git', ['ls-files', '-z'], { cwd: root, encoding: 'utf8', windowsHide: true });
  if (fs.existsSync(path.join(root, '.git')) && git.status !== 0) throw Error('Cannot inspect tracked files');
  if (fs.existsSync(path.join(root, '.git'))) for (const name of git.stdout.split('\0').filter(Boolean)) if (!expected.has(name)) throw Error('Unreviewed tracked file: ' + name);
  if (allFiles) {
    function walk(folder, prefix = '') { for (const entry of fs.readdirSync(folder, { withFileTypes: true })) { const name = prefix + entry.name; if (entry.isSymbolicLink()) throw Error('Linked entry: ' + name); if (entry.isDirectory()) walk(path.join(folder, entry.name), name + '/'); else if (!expected.has(name)) throw Error('Unexpected export file: ' + name); } }
    walk(root);
  }
  console.log('Public source audit passed: ' + Object.keys(manifest.files).length + ' text files, zero bundled artwork.');
  return true;
}
if (require.main === module) { try { verify(path.resolve(__dirname, '..')); } catch (e) { console.error(e.message); process.exitCode = 1; } }
module.exports = { inspectFile, verify };
