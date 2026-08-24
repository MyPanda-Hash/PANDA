# PanelX Proxy SDK

| 属性 | 内容 |
|---|---|
| 文档类型 | SDK 说明 |
| 适用场景 | 通用架构与 SDK |
| 维护状态 | 生效 |
| 最后整理 | 2026-08-24 |
| 文档导航 | [文档中心](../../../../docs/README.md) |

`core/sdk/` is the reusable, framework-independent client for the Light MES PanelX gateway. It depends only on an injected HTTP client and can be copied into another ESM project.

The sibling `core/sdk.js` file is the Light MES adapter. It injects the project's Axios instance, checks `VITE_PANELX_PROXY`, and retains the existing `initSdk`, `sdkLogin`, and `requireAuthed` API.

## Create a client

```js
import axios from 'axios'
import { createPanelxProxySdk } from './core/sdk/index.js'

const httpClient = axios.create({ baseURL: '/api' })
const sdk = createPanelxProxySdk({ httpClient, basePath: '/panelx' })

const response = await sdk.api.queryFormDataList({
  panelCode: 'MANU_ORDER',
  pageNo: 1,
  pageSize: 20,
})
```

The injected client must expose Axios-compatible `get(url, config)` and `post(url, body)` methods. Its response interceptor should return the response body because the gateway preserves the PanelX `{ state, msg, data }` envelope.

## Public API

| Method | Gateway request |
|---|---|
| `api.getPanelConfig(panelCode)` | `GET /panelx/getPanelConfig` |
| `api.getPermMatrix(params)` | `GET /panelx/getPermMatrix` |
| `api.getNewFormPermMatrix(params)` | `GET /panelx/getNewFormPermMatrix` |
| `api.getFormDescriptor(params)` | `GET /panelx/getFormDescriptor` |
| `api.queryFormDataList(params)` | `POST /panelx/queryFormDataList` |
| `api.callButton(params)` | `POST /panelx/callButton` |
| `api.deleteForms(params)` | `POST /panelx/deleteForms` |

`user.isAuthenticated()` and `user.login()` intentionally report success because platform credentials and JWT lifecycle belong to the Spring Boot gateway. HTTP failures become `PanelxProxyError`; `getErrorDetail(error)` returns stable `errorCode`, `errorDescription`, `error`, and `status` fields.
