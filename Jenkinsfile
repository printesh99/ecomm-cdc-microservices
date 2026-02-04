pipeline {
  agent any

  parameters {
    booleanParam(name: 'RUN_ALL', defaultValue: false, description: 'Run DB init + restart connector + smoke test + verify topics')
    booleanParam(name: 'SKIP_BUILD', defaultValue: true, description: 'Skip build/tag steps when only testing CDC/outbox')
    booleanParam(name: 'RUN_DB_INIT', defaultValue: false, description: 'Trigger DB init hook (creates publication/permissions)')
    booleanParam(name: 'RESTART_CONNECTOR', defaultValue: false, description: 'Trigger Debezium connector restart via GitOps')
    booleanParam(name: 'RUN_SMOKE_TEST', defaultValue: true, description: 'Trigger outbox smoke test job')
    booleanParam(name: 'VERIFY_TOPICS', defaultValue: true, description: 'Verify Kafka topics after sync')
    string(name: 'TOPIC_PREFIX', defaultValue: 'ecomm', description: 'Topic prefix to verify (ex: ecomm)')
  }

  environment {
    REGISTRY = "image-registry.openshift-image-registry.svc:5000"
    IMAGE_NS = "ecomm"

    GITOPS_REPO = "https://github.com/printesh99/ecomm-cdc-gitops.git"
    GITOPS_BRANCH = "main"
    GITOPS_OVERLAY_PATH = "apps/overlays/dev/kustomization.yaml"
    GITOPS_OUTBOX_JOB_PATH = "apps/overlays/dev-db/outbox-test-job.yaml"
    GITOPS_DB_INIT_JOB_PATH = "apps/overlays/dev-db/outbox-db-init-job.yaml"
    GITOPS_OUTBOX_CONNECTOR_PATH = "apps/overlays/dev/outbox-connector.yaml"
    KAFKA_BOOTSTRAP = "ecomm-kafka-kafka-bootstrap.ecomm-streaming.svc.cluster.local:9092"

    GIT_AUTHOR_NAME = "jenkins"
    GIT_AUTHOR_EMAIL = "jenkins@local"
    GIT_COMMITTER_NAME = "jenkins"
    GIT_COMMITTER_EMAIL = "jenkins@local"
  }

  stages {
    stage("Checkout") {
      steps {
        checkout scm
        script {
          GIT_SHA = sh(script: "git rev-parse --short HEAD", returnStdout: true).trim()
          echo "GIT_SHA=${GIT_SHA}"
        }
      }
    }

    stage("oc login (in-cluster SA)") {
      steps {
        sh '''
          set -e
          TOKEN=$(cat /var/run/secrets/kubernetes.io/serviceaccount/token)
          CACRT=/var/run/secrets/kubernetes.io/serviceaccount/ca.crt
          oc login https://kubernetes.default.svc --token="$TOKEN" --certificate-authority="$CACRT" >/dev/null
          oc whoami
        '''
      }
    }

    stage("Build & Tag Images (Binary Builds)") {
      when { expression { return !params.SKIP_BUILD } }
      steps {
        script {
          def services = [
            [name:"catalog-service",  dir:"services/catalog"],
            [name:"cart-service",     dir:"services/cart"],
            [name:"orders-service",   dir:"services/orders"],
            [name:"payment-service",  dir:"services/payment"],
            [name:"shipping-service", dir:"services/shipping"]
          ]

          for (s in services) {
            sh """
              set -e
              echo "==> Building ${s.name} from ${s.dir}"

              # Create BuildConfig once
              oc -n ${IMAGE_NS} get bc/${s.name} >/dev/null 2>&1 || \\
                oc -n ${IMAGE_NS} new-build --name=${s.name} --strategy=docker --binary=true

              # Start binary build from folder
              oc -n ${IMAGE_NS} start-build ${s.name} --from-dir=${s.dir} --follow

              # Tag imagestream with git sha
              oc -n ${IMAGE_NS} tag ${s.name}:latest ${s.name}:${GIT_SHA}

              # show result
              oc -n ${IMAGE_NS} get is/${s.name} -o jsonpath='{.status.dockerImageRepository}{"\\n"}' || true
            """
          }
        }
      }
    }

    stage("Update GitOps (bump image tags)") {
      when { expression { return !params.SKIP_BUILD } }
      steps {
        withCredentials([usernamePassword(credentialsId: 'github-pat', usernameVariable: 'GIT_USER', passwordVariable: 'GIT_PAT')]) {
          sh '''
            set -e
            rm -rf /tmp/gitops && mkdir -p /tmp/gitops
            cd /tmp/gitops

            git clone ${GITOPS_REPO} repo
            cd repo
            git checkout ${GITOPS_BRANCH}
            git config user.name "${GIT_COMMITTER_NAME}"
            git config user.email "${GIT_COMMITTER_EMAIL}"
            git pull --rebase origin ${GITOPS_BRANCH}

            # Replace newTag for each service (expects images: entries exist)
            for svc in catalog-service cart-service orders-service payment-service shipping-service; do
              echo "Updating tag for $svc to ${GIT_SHA}"
              perl -0777 -i -pe "s/(name:\\s*$svc\\s*\\n\\s*newName:.*\\n\\s*newTag:)\\s*.*$/\\1 ${GIT_SHA}/ms" ${GITOPS_OVERLAY_PATH} || true
            done

            git add ${GITOPS_OVERLAY_PATH}
            git commit -m "ci: bump images to ${GIT_SHA}" || echo "No changes to commit"
            git push https://${GIT_USER}:${GIT_PAT}@github.com/printesh99/ecomm-cdc-gitops.git ${GITOPS_BRANCH} || {
              echo "Push failed, rebasing and retrying..."
              git pull --rebase origin ${GITOPS_BRANCH}
              git push https://${GIT_USER}:${GIT_PAT}@github.com/printesh99/ecomm-cdc-gitops.git ${GITOPS_BRANCH}
            }
          '''
        }
      }
    }

    stage("Trigger DB Init Hook") {
      when { expression { return params.RUN_ALL || params.RUN_DB_INIT } }
      steps {
        withCredentials([usernamePassword(credentialsId: 'github-pat', usernameVariable: 'GIT_USER', passwordVariable: 'GIT_PAT')]) {
          sh '''
            set -e
            # delete existing job so Argo can recreate it on sync
            oc -n db delete job outbox-db-init --ignore-not-found
            oc -n db delete pod -l job-name=outbox-db-init --grace-period=0 --force --ignore-not-found
            rm -rf /tmp/gitops && mkdir -p /tmp/gitops
            cd /tmp/gitops

            git clone ${GITOPS_REPO} repo
            cd repo
            git checkout ${GITOPS_BRANCH}
            git config user.name "${GIT_COMMITTER_NAME}"
            git config user.email "${GIT_COMMITTER_EMAIL}"
            git pull --rebase origin ${GITOPS_BRANCH}

            if [ ! -f ${GITOPS_DB_INIT_JOB_PATH} ]; then
              echo "DB init job not found at ${GITOPS_DB_INIT_JOB_PATH}; skipping."
              exit 0
            fi

            RUN_ID=$(date +%s)
            perl -0777 -i -pe "s/run_id: \\".*\\"/run_id: \\"$RUN_ID\\"/g" ${GITOPS_DB_INIT_JOB_PATH} || true

            git add ${GITOPS_DB_INIT_JOB_PATH}
            git commit -m "chore: rerun db init $RUN_ID" || echo "No changes to commit"
            git push https://${GIT_USER}:${GIT_PAT}@github.com/printesh99/ecomm-cdc-gitops.git ${GITOPS_BRANCH} || {
              echo "Push failed, rebasing and retrying..."
              git pull --rebase origin ${GITOPS_BRANCH}
              RUN_ID=$(date +%s)
              perl -0777 -i -pe "s/run_id: \\".*\\"/run_id: \\"$RUN_ID\\"/g" ${GITOPS_DB_INIT_JOB_PATH} || true
              git add ${GITOPS_DB_INIT_JOB_PATH}
              git commit -m "chore: rerun db init $RUN_ID" || echo "No changes to commit"
              git push https://${GIT_USER}:${GIT_PAT}@github.com/printesh99/ecomm-cdc-gitops.git ${GITOPS_BRANCH}
            }
          '''
        }
      }
    }

    stage("Restart Debezium Connector") {
      when { expression { return params.RUN_ALL || params.RESTART_CONNECTOR } }
      steps {
        withCredentials([usernamePassword(credentialsId: 'github-pat', usernameVariable: 'GIT_USER', passwordVariable: 'GIT_PAT')]) {
          sh '''
            set -e
            rm -rf /tmp/gitops && mkdir -p /tmp/gitops
            cd /tmp/gitops

            git clone ${GITOPS_REPO} repo
            cd repo
            git checkout ${GITOPS_BRANCH}
            git config user.name "${GIT_COMMITTER_NAME}"
            git config user.email "${GIT_COMMITTER_EMAIL}"
            git pull --rebase origin ${GITOPS_BRANCH}

            if [ ! -f ${GITOPS_OUTBOX_CONNECTOR_PATH} ]; then
              echo "Connector file not found at ${GITOPS_OUTBOX_CONNECTOR_PATH}; skipping."
              exit 0
            fi

            RUN_ID=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
            perl -0777 -i -pe "s/strimzi.io\\/restart: \\".*\\"/strimzi.io\\/restart: \\"$RUN_ID\\"/g" ${GITOPS_OUTBOX_CONNECTOR_PATH} || true

            git add ${GITOPS_OUTBOX_CONNECTOR_PATH}
            git commit -m "chore: restart connector $RUN_ID" || echo "No changes to commit"
            git push https://${GIT_USER}:${GIT_PAT}@github.com/printesh99/ecomm-cdc-gitops.git ${GITOPS_BRANCH} || {
              echo "Push failed, rebasing and retrying..."
              git pull --rebase origin ${GITOPS_BRANCH}
              RUN_ID=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
              perl -0777 -i -pe "s/strimzi.io\\/restart: \\".*\\"/strimzi.io\\/restart: \\"$RUN_ID\\"/g" ${GITOPS_OUTBOX_CONNECTOR_PATH} || true
              git add ${GITOPS_OUTBOX_CONNECTOR_PATH}
              git commit -m "chore: restart connector $RUN_ID" || echo "No changes to commit"
              git push https://${GIT_USER}:${GIT_PAT}@github.com/printesh99/ecomm-cdc-gitops.git ${GITOPS_BRANCH}
            }
          '''
        }
      }
    }

    stage("Trigger Outbox Smoke Test") {
      when { expression { return params.RUN_ALL || params.RUN_SMOKE_TEST } }
      steps {
        withCredentials([usernamePassword(credentialsId: 'github-pat', usernameVariable: 'GIT_USER', passwordVariable: 'GIT_PAT')]) {
          sh '''
            set -e
            # delete existing job so Argo can recreate it on sync
            oc -n db delete job outbox-smoke-test --ignore-not-found
            oc -n db delete pod -l job-name=outbox-smoke-test --grace-period=0 --force --ignore-not-found
            rm -rf /tmp/gitops && mkdir -p /tmp/gitops
            cd /tmp/gitops

            git clone ${GITOPS_REPO} repo
            cd repo
            git checkout ${GITOPS_BRANCH}
            git config user.name "${GIT_COMMITTER_NAME}"
            git config user.email "${GIT_COMMITTER_EMAIL}"
            git pull --rebase origin ${GITOPS_BRANCH}

            if [ ! -f ${GITOPS_OUTBOX_JOB_PATH} ]; then
              echo "Outbox job not found at ${GITOPS_OUTBOX_JOB_PATH}; skipping."
              exit 0
            fi

            RUN_ID=$(date +%s)
            perl -0777 -i -pe "s/run_id: \\".*\\"/run_id: \\"$RUN_ID\\"/g" ${GITOPS_OUTBOX_JOB_PATH} || true

            git add ${GITOPS_OUTBOX_JOB_PATH}
            git commit -m "chore: rerun outbox smoke test $RUN_ID" || echo "No changes to commit"
            git push https://${GIT_USER}:${GIT_PAT}@github.com/printesh99/ecomm-cdc-gitops.git ${GITOPS_BRANCH} || {
              echo "Push failed, rebasing and retrying..."
              git pull --rebase origin ${GITOPS_BRANCH}
              RUN_ID=$(date +%s)
              perl -0777 -i -pe "s/run_id: \\".*\\"/run_id: \\"$RUN_ID\\"/g" ${GITOPS_OUTBOX_JOB_PATH} || true
              git add ${GITOPS_OUTBOX_JOB_PATH}
              git commit -m "chore: rerun outbox smoke test $RUN_ID" || echo "No changes to commit"
              git push https://${GIT_USER}:${GIT_PAT}@github.com/printesh99/ecomm-cdc-gitops.git ${GITOPS_BRANCH}
            }
          '''
        }
      }
    }

    stage("Verify Kafka Topics") {
      when { expression { return params.RUN_ALL || params.VERIFY_TOPICS } }
      steps {
        sh '''
          set -e
          NS="ecomm-streaming"
          BOOTSTRAP="${KAFKA_BOOTSTRAP}"
          PREFIX="${TOPIC_PREFIX}"
          if [ -z "$PREFIX" ]; then
            PREFIX="ecomm"
          fi

          POD=$(oc -n ${NS} get pods -l strimzi.io/name=ecomm-kafka-kafka -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)
          if [ -z "$POD" ]; then
            POD=$(oc -n ${NS} get pods -l strimzi.io/cluster=ecomm-kafka -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)
          fi
          if [ -z "$POD" ]; then
            echo "Kafka pod not found in ${NS}"
            oc -n ${NS} get pods
            exit 1
          fi

          echo "Using Kafka pod: $POD"
          echo "Topic prefix: $PREFIX"
          TOPICS=$(oc -n ${NS} exec "$POD" -- /opt/kafka/bin/kafka-topics.sh --bootstrap-server "$BOOTSTRAP" --list | sort || true)
          echo "$TOPICS"

          echo "$TOPICS" | grep -q "^${PREFIX}\\." || {
            echo "No topics found with prefix '${PREFIX}'."
            exit 1
          }
        '''
      }
    }
  }
}
