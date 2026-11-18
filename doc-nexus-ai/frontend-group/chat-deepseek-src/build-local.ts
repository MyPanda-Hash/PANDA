import { defineConfig } from '@rspack/cli'
import { createConfig } from './rspack.config'

export default defineConfig(createConfig({ apiOrigin: '/ai-api' }))
