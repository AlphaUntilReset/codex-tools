// Same short environment path as start.ps1; printing this does not install anything.
const path=require('node:path'),os=require('node:os'),crypto=require('node:crypto');
const root=path.resolve(__dirname,'..');
const key=crypto.createHash('sha256').update(root.replace(/[\\/]+$/,'').toLowerCase(),'utf8').digest('hex').slice(0,12);
const python=process.env.CODEX_TOOLS_PYTHON || path.join(process.env.USERPROFILE || os.homedir(),'.codex-tools','v',key,'Scripts','python.exe');
if(require.main===module)console.log(python);
module.exports={python};
