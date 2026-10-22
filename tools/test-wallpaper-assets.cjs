const path=require('node:path');const {spawnSync}=require('node:child_process');
const {python}=require('./python-env.cjs');
const result=spawnSync(python,[path.resolve(__dirname,'../components/codex-wallpaper/源码/test_prepare_images.py')],{stdio:'inherit',windowsHide:true});
if(result.error)throw result.error;process.exitCode=result.status===null?1:result.status;
