export default {
  colors: {
    presets: {
      light: {
        backgroundPrimary: '#F4F6F8',
        backgroundSecondary: '#FFFFFF',
        backgroundCardPrimary: '#F7F9F9',
        backgroundCardSecondary: '#ECFDE6',
        success: '#228200',
        info: '#158DE3',
        danger: '#E42222',
        warning: '#FFD43A'
      },
      dark: {
        backgroundCardPrimary: '#111827',
        backgroundCardSecondary: '#0f172a'
      }
    }
  },
  breakpoint: {
    enabled: true,
    bodyClass: true,
    thresholds: { xs: 0, sm: 320, md: 640, lg: 1024, xl: 1440 }
  },
  components: {
    VaInput: { inputAriaLabel: '' },
    VaCounter: { manualInput: true },
    VaModal: {
      mobileFullscreen: false,
      maxHeight: 'calc(100% - 2rem)',
      okText: 'Подтвердить',
      cancelText: 'Отмена'
    },
    VaDataTable: {
      disableClientSideSorting: true,
      noDataHtml: 'Нет записей'
    },
    VaSelect: { ariaLabel: 'Выберите значение', noOptionsText: 'Нет вариантов' }
  },
  i18n: {
    search: 'Поиск',
    ok: 'Подтвердить',
    cancel: 'Отмена',
    close: 'Закрыть',
    closeToast: 'Закрыть уведомление',
    closeAlert: 'Закрыть сообщение',
    reset: 'Очистить',
    toggleDropdown: 'Открыть список',
    decreaseCounter: 'Уменьшить',
    increaseCounter: 'Увеличить',
    counterValue: 'Значение',
    select: 'Выберите значение',
    selectedOption: 'Выбрано',
    noSelectedOption: 'Значение не выбрано',
    optionsFilter: 'Поиск по вариантам',
    inputField: 'Поле ввода',
    loading: 'Загрузка'
  }
}
