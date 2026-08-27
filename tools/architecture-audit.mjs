import fs from 'node:fs'
import path from 'node:path'
import process from 'node:process'
import { fileURLToPath } from 'node:url'

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const core = path.join(root, 'frontend', 'src', 'core')
const errors = []

function sourceFiles(directory, output = []) {
  for (const entry of fs.readdirSync(directory, { withFileTypes: true })) {
    const target = path.join(directory, entry.name)
    if (entry.isDirectory()) sourceFiles(target, output)
    else if (/\.(?:js|vue)$/.test(entry.name)) output.push(target)
  }
  return output
}

for (const file of sourceFiles(core)) {
  const source = fs.readFileSync(file, 'utf8')
  if (/from\s+['"](?:@\/business|\.\.\/business|\.\.\/\.\.\/business)/.test(source)) {
    errors.push(`${path.relative(root, file)} imports the business layer`)
  }
}

const controller = fs.readFileSync(
  path.join(root, 'backend', 'src', 'main', 'java', 'com', 'mes', 'controller', 'PxController.java'),
  'utf8',
)
if (controller.includes('com.mes.service.PxService')) {
  errors.push('PxController depends on PxService instead of PanelRuntimeService')
}

if (errors.length) {
  console.error(`Architecture audit failed (${errors.length}):`)
  for (const error of errors) console.error(`- ${error}`)
  process.exit(1)
}

console.log('Architecture audit passed: panel core is isolated from business adapters')
