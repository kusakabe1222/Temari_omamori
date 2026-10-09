// data-confirm が付いたフォーム（削除など）は、送る前に確認ダイアログを出す
document.addEventListener("submit", function (event) {
  var form = event.target;
  if (form.dataset && form.dataset.confirm && !window.confirm(form.dataset.confirm)) {
    event.preventDefault();
  }
});
