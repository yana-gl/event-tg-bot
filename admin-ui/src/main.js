import { createApp } from 'vue'
import { createPinia } from 'pinia'
import {
  createVuesticEssential,
  VaAlert, VaAvatar, VaButton, VaCard, VaCardContent, VaCardTitle, VaChip,
  VaCollapse, VaCounter, VaDataTable, VaDivider, VaForm, VaIcon, VaInput,
  VaLayout, VaNavbar, VaProgressCircle, VaSelect, VaSidebar, VaSidebarItem,
  VaSidebarItemContent, VaSidebarItemTitle, VaModalPlugin, VaToastPlugin,
  BreakpointConfigPlugin, VaDropdownPlugin
} from 'vuestic-ui'
import 'vuestic-ui/styles/essential.css'
import 'vuestic-ui/styles/typography.css'
import '@fontsource/inter/cyrillic-400.css'
import '@fontsource/inter/cyrillic-600.css'
import '@fontsource/inter/cyrillic-700.css'
import '@fontsource/inter/latin-400.css'
import '@fontsource/inter/latin-600.css'
import '@fontsource/inter/latin-700.css'
import '@fontsource/material-icons'

import App from './App.vue'
import router from './router'
import vuesticConfig from './vuestic-config'
import './styles.css'

createApp(App)
  .use(createPinia())
  .use(router)
  .use(createVuesticEssential({
    components: {
      VaAlert, VaAvatar, VaButton, VaCard, VaCardContent, VaCardTitle, VaChip,
      VaCollapse, VaCounter, VaDataTable, VaDivider, VaForm, VaIcon, VaInput,
      VaLayout, VaNavbar, VaProgressCircle, VaSelect, VaSidebar, VaSidebarItem,
      VaSidebarItemContent, VaSidebarItemTitle
    },
    plugins: { BreakpointConfigPlugin, VaDropdownPlugin, VaModalPlugin, VaToastPlugin },
    config: vuesticConfig
  }))
  .mount('#app')
