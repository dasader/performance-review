# 서버 갱신 한 방: 원격 최신을 받아 이미지를 다시 굽고 컨테이너를 재생성한다.
#
# --build 가 필요한 이유: 프론트엔드는 web 컨테이너 안에서 빌드돼 nginx가 정적 파일로
# 서빙한다 — 이미지를 다시 굽지 않으면 frontend/ 변경이 화면에 반영되지 않는다.
#
# --force-recreate 가 필요한 이유: 이미지가 그대로면 compose는 컨테이너를 재사용하고
# env_file(.env)도 다시 읽지 않는다. .env만 고친 경우 옛 값이 조용히 살아남는다.
#
# db는 재생성 대상에서 뺐다 — 데이터는 볼륨에 있어 안전하지만 굳이 껐다 켤 이유가 없고,
# api의 depends_on 으로 어차피 기동된다. 마이그레이션은 api entrypoint가
# alembic upgrade head 로 자동 실행하므로 여기서 따로 부르지 않는다.
.PHONY: rebuild
rebuild:
	git pull --ff-only
	docker compose up -d --build --force-recreate api web
	docker compose ps
