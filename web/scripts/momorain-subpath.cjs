// 用语法树识别原生网络调用与静态资源，不改 Next Link/router（它们自动处理 basePath）。
const ts = require('typescript');
const fs = require('fs');
const path = require('path');
let changed = 0;
function walk(dir) {
 for (const entry of fs.readdirSync(dir, {withFileTypes:true})) {
  const file = path.join(dir, entry.name);
  if (entry.isDirectory()) { if (!['api','tests','node_modules'].includes(entry.name)) walk(file); continue; }
  if (!/\.tsx?$/.test(file) || /\.test\.|\.spec\./.test(file) || file.endsWith('base-path.ts')) continue;
  let source = fs.readFileSync(file,'utf8');
  const ast = ts.createSourceFile(file,source,ts.ScriptTarget.Latest,true);
  const edits=[]; const imports = new Set();
  function visit(node) {
   if (ts.isCallExpression(node) && ts.isIdentifier(node.expression) && node.expression.text==='fetch') {
    edits.push([node.expression.getStart(ast),node.expression.end,'appFetch']); imports.add('appFetch');
   }
   if (ts.isJsxAttribute(node) && ['src','poster','href'].includes(node.name.getText(ast)) && node.initializer && ts.isStringLiteral(node.initializer)) {
    const value=node.initializer.text;
    const element=node.parent.parent;
    const tag=element.tagName?.getText(ast);
    if (value.startsWith('/') && !value.startsWith('//') && (node.name.text!=='href' || tag==='a' || tag==='link')) {
      edits.push([node.initializer.getStart(ast),node.initializer.end,`{withBasePath(${JSON.stringify(value)})}`]); imports.add('withBasePath');
    }
   }
   ts.forEachChild(node,visit);
  }
  visit(ast);
  if (!edits.length) continue;
  for (const [start,end,replacement] of edits.sort((a,b)=>b[0]-a[0])) source=source.slice(0,start)+replacement+source.slice(end);
  // use client / use server 指令必须继续是第一条语句。
  const directive=/^(?:["']use (?:client|server)["'];?\s*)/;
  const match=source.match(directive); const offset=match ? match[0].length : 0;
  source=source.slice(0,offset)+`\nimport { ${[...imports].join(', ')} } from "@/lib/base-path";\n`+source.slice(offset);
  fs.writeFileSync(file,source); changed++;
 }
}
for (const dir of ['app','components','features','hooks','lib','shared','context','i18n']) walk(dir);
console.log(`适配了 ${changed} 个文件的原生网络调用和资源路径`);
