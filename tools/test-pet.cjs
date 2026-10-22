const fs=require('node:fs'),path=require('node:path'),os=require('node:os');
const {spawnSync}=require('node:child_process');
const component=path.resolve(__dirname,'../components/desktop-pet');
const {python}=require('./python-env.cjs');
const data=fs.mkdtempSync(path.join(os.tmpdir(),'CodexToolsPetTest-'));
// Separate Qt suites so native event-loop state cannot leak between test files.
for(const file of fs.readdirSync(path.join(component,'tests')).filter(x=>/^test_.*\.py$/.test(x)).sort()){
  const result=spawnSync(python,['-m','pytest','-q','tests/'+file],{cwd:component,stdio:'inherit',windowsHide:true,
    env:{...process.env,CODEXTOOLS_PET_DATA_DIR:path.join(data,file),CODEX_HOME:path.join(data,'empty-codex-home'),QT_QPA_PLATFORM:'offscreen'}});
  if(result.error)throw result.error;
  if(result.status!==0){process.exitCode=result.status===null?1:result.status;break;}
}
