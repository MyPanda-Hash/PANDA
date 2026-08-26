import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import process from 'node:process'
import { spawnSync } from 'node:child_process'
import { fileURLToPath } from 'node:url'

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const frontend = path.join(root, 'frontend')
const backend = path.join(root, 'backend')

function command(name) {
  return process.platform === 'win32' ? `${name}.cmd` : name
}

function run(command, args, cwd, env = process.env) {
  console.log(`\n> ${command} ${args.join(' ')}`)
  const isWindowsScript = process.platform === 'win32' && /\.cmd$/i.test(command)
  const executable = isWindowsScript ? (process.env.ComSpec || 'cmd.exe') : command
  const executableArgs = isWindowsScript
    ? ['/d', '/c', 'call', command, ...args]
    : args
  const result = spawnSync(executable, executableArgs, {
    cwd,
    env,
    stdio: 'inherit',
    shell: false,
  })
  if (result.error) throw result.error
  if (result.status !== 0) throw new Error(`命令失败（退出码 ${result.status}）：${command}`)
}

function javaMajor(javaHome) {
  const java = path.join(javaHome, 'bin', process.platform === 'win32' ? 'java.exe' : 'java')
  const javac = path.join(javaHome, 'bin', process.platform === 'win32' ? 'javac.exe' : 'javac')
  if (!fs.existsSync(javac)) return 0
  const result = spawnSync(java, ['-version'], { encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'] })
  const text = `${result.stdout || ''}\n${result.stderr || ''}`
  const match = text.match(/version\s+"([0-9]+)/i)
  if (!match) return 0
  const major = Number(match[1])
  return major === 1 ? 0 : major
}

function findJavaHome() {
  const candidates = []
  if (process.env.JAVA_HOME) candidates.push(process.env.JAVA_HOME)
  const roots = process.platform === 'win32'
    ? ['C:\\Program Files\\Java', 'D:\\Program Files\\Java', 'C:\\Program Files\\Eclipse Adoptium', 'D:\\Program Files\\Eclipse Adoptium']
    : ['/usr/lib/jvm', '/Library/Java/JavaVirtualMachines']
  for (const base of roots) {
    if (!fs.existsSync(base)) continue
    for (const entry of fs.readdirSync(base)) candidates.push(path.join(base, entry))
  }
  const valid = candidates
    .map((candidate) => ({ candidate, major: javaMajor(candidate) }))
    .filter(({ major }) => major >= 17)
    .sort((a, b) => b.major - a.major)
  if (!valid.length) throw new Error('未找到 JDK 17+，请安装后重试')
  return valid[0]
}

try {
  run(process.execPath, ['tools/docs-audit.mjs'], root)
  run(command('npm'), ['run', 'build'], frontend)

  const java = findJavaHome()
  const maven = path.join(root, 'tools', 'apache-maven-3.9.9', 'bin', command('mvn'))
  const env = { ...process.env, JAVA_HOME: java.candidate }
  if (process.platform === 'win32') env.Path = `${path.join(java.candidate, 'bin')};${env.Path || ''}`
  run(maven, ['-s', path.join(root, 'tools', 'settings.xml'), '-q', 'clean', 'package', '-DskipTests'], backend, env)
  console.log(`\n项目校验通过（JDK ${java.major}，CPU ${os.arch()}）`)
} catch (error) {
  console.error(`\n项目校验失败：${error.message}`)
  process.exitCode = 1
}
