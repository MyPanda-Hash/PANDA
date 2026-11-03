const REQUIRED_METHODS = [
  'getPanelConfig',
  'getPermMatrix',
  'getNewFormPermMatrix',
  'getFormDescriptor',
  'queryFormDataList',
  'callButton',
  'deleteForms',
  'recognizeFormImage',
  'queryRefRows',
  'refPanelName',
  'refColumns',
  'refLabelOf',
  'fieldOptions',
  'fillCurrentStock',
  'roundDecimal',
  'errMsg',
]

let activeRuntime = null

/**
 * Register the application adapter used by the reusable panel views.
 * MES and PLM can provide different adapters while sharing the same renderer.
 */
export function installPanelRuntime(runtime) {
  if (!runtime || typeof runtime !== 'object') {
    throw new TypeError('Panel runtime must be an object')
  }
  const missing = REQUIRED_METHODS.filter((name) => typeof runtime[name] !== 'function')
  if (missing.length) {
    throw new TypeError(`Panel runtime is missing: ${missing.join(', ')}`)
  }
  activeRuntime = Object.freeze({ ...runtime })
  return activeRuntime
}

export function usePanelRuntime() {
  if (!activeRuntime) {
    throw new Error('Panel runtime has not been installed')
  }
  return activeRuntime
}

export const PANEL_RUNTIME_METHODS = Object.freeze([...REQUIRED_METHODS])
