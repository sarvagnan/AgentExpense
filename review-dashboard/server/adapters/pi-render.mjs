// Inspect real Pi discovery and extension registration without auth or a model.
import {pathToFileURL} from 'node:url';
const {DefaultResourceLoader}=await import(process.env.EXPENSES_PI_PACKAGE ? pathToFileURL(process.env.EXPENSES_PI_PACKAGE+'/dist/index.js').href : '@earendil-works/pi-coding-agent');
const loader=new DefaultResourceLoader({cwd:process.cwd(),agentDir:process.env.PI_CODING_AGENT_DIR,
  additionalExtensionPaths:[new URL('./pi-lookup.mjs',import.meta.url).pathname],
  noExtensions:true,noSkills:true,noPromptTemplates:true,noThemes:true,noContextFiles:true});
await loader.reload();
const extensions=loader.getExtensions();
if(extensions.errors.length)throw new Error('Extension load failed');
console.log(JSON.stringify({
  tools:extensions.extensions.flatMap(e=>[...e.tools.keys()]),mcp:['ledger'],
  instructions:loader.getAgentsFiles().agentsFiles.map(x=>x.path),skills:loader.getSkills().skills.map(x=>x.name),
  plugins:[],hooks:extensions.extensions.flatMap(e=>[...e.handlers.keys()]),commands:extensions.extensions.flatMap(e=>[...e.commands.keys()]),settings:[],extensions:extensions.extensions.map(e=>e.path),
  prompts:loader.getPrompts().prompts.map(x=>x.name),themes:loader.getThemes().themes.map(x=>x.name)
}));
